"""Project profiles: what the coding agent needs to know about a kind of project that is not plain Python.

A profile says how the project is recognised, how it is verified, and where the sources of its libraries are, so
the model can look an API up instead of guessing it. To support another toolchain (Unity, Godot, Fabric, a C++
engine) add a Profile to PROFILES; the first one that matches a project wins, so put the special before the general.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_HITS = 40  # lines returned by one search_sources
MAX_READ = 12000  # characters returned by one read_source
GRADLE = ".\\gradlew.bat" if os.name == "nt" else "./gradlew"


def _contains(file: str, text: str) -> Callable[[Path], bool]:
    def check(root: Path) -> bool:
        path = root / file
        return path.is_file() and text in path.read_text(encoding="utf-8", errors="replace")

    return check


def _any(*checks: Callable[[Path], bool]) -> Callable[[Path], bool]:
    return lambda root: any(c(root) for c in checks)


def src_dirs(root: Path) -> list[Path]:
    """src/ of the project and of its modules (a Gradle build with core/, primal/ ... each with its own src/)."""
    return [d for d in [root / "src", *root.glob("*/src")] if d.is_dir()]


def _code_contains(text: str, suffix: str = ".java") -> Callable[[Path], bool]:
    """Whether a source file under src/ has the text (a mod with game tests, a data generator)."""

    def check(root: Path) -> bool:
        files = (p for d in src_dirs(root) for p in d.rglob("*" + suffix))
        return any(text in p.read_text(encoding="utf-8", errors="replace") for p in files)

    return check


@dataclass(frozen=True)
class Task:
    """A longer command of the project that the model may run as a tool of its own: the game's own tests, a data
    generator. Offered only when `available` says the project has what the command needs."""

    tool: str
    description: str
    command: str
    available: Callable[[Path], bool]
    timeout: float = 900


@dataclass(frozen=True)
class Profile:
    name: str
    matches: Callable[[Path], bool]
    verify: str | Callable[[Path], str]  # the command that compiles and tests the project
    sources: tuple[str, ...] = ()  # globs, relative to the project, of archives with library sources
    # False: most of the code needs the game or engine to run, so a step is done when the project compiles and a
    # test is written only for logic that runs without it (the user can still ask for tests with every step)
    tests_required: bool = True
    logs: tuple[str, ...] = ()  # globs of the logs and crash reports a run of the game or program writes
    # a check of the files the compiler does not see (resources, assets): (errors, warnings); errors fail verify
    check: Callable[[Path], tuple[list[str], list[str]]] | None = None
    fails_on: str = ""  # a regex of output that means failure although the command exits with 0 (Godot)
    tasks: tuple[Task, ...] = ()


def _minecraft_check(root: Path) -> tuple[list[str], list[str]]:
    from local_ai.coder import minecraft

    return minecraft.check(root)


def _python_game_verify(root: Path) -> str:
    from local_ai.coder import verify as vf

    python = vf.project_python(root)
    return f"{python} -m pytest -x -q --tb=short" if vf.has_tests(root) else f"{python} -m compileall -q -x venv ."


MOD_LOGS = tuple(f"{m}run/{f}" for m in ("", "*/") for f in ("crash-reports/crash-*.txt", "logs/latest.log"))
GODOT_ERRORS = r"SCRIPT ERROR|Parse Error|ERROR: "

PROFILES = [
    Profile(
        "neoforge-mod",
        _contains("build.gradle", "net.neoforged"),
        f"{GRADLE} compileJava test --console=plain -q",
        ("build/moddev/artifacts/*-sources.jar", "*/build/moddev/artifacts/*-sources.jar"),
        tests_required=False,
        logs=MOD_LOGS,
        check=_minecraft_check,
        tasks=(
            Task(
                "run_game_tests",
                "Run the mod's GameTests (@GameTest) in a headless game server: they check behaviour in a real "
                "world (an entity's health, a block's drop). Takes minutes; run it at the end, not after every step.",
                f"{GRADLE} runGameTestServer --console=plain -q",
                _code_contains("@GameTest"),
            ),
            Task(
                "run_datagen",
                "Run the mod's data generators (runData): they write the JSON (models, blockstates, loot tables, "
                "recipes, lang) into src/generated/resources. Change the generator, not the generated files.",
                f"{GRADLE} runData --console=plain -q",
                _code_contains("GatherDataEvent"),
            ),
        ),
    ),
    Profile(
        "fabric-mod",
        _contains("build.gradle", "fabric-loom"),
        f"{GRADLE} compileJava test --console=plain -q",
        (".gradle/loom-cache/**/*-sources.jar",),  # after ./gradlew genSources
        tests_required=False,
        logs=MOD_LOGS,
        check=_minecraft_check,
        tasks=(
            Task(
                "run_datagen",
                "Run the mod's data generators (runDatagen): they write the generated JSON resources. Change the "
                "generator, not the generated files.",
                f"{GRADLE} runDatagen --console=plain -q",
                _code_contains("DataGeneratorEntrypoint"),
            ),
            Task(
                "run_game_tests",
                "Run the mod's game tests in a headless server (runGametest). Takes minutes; run it at the end.",
                f"{GRADLE} runGametest --console=plain -q",
                _any(_contains("build.gradle", "runGametest"), _contains("build.gradle", "gametest")),
            ),
        ),
    ),
    Profile(
        "godot",
        lambda root: (root / "project.godot").is_file(),
        "godot --headless --path . --import",  # parses every script; needs godot (4.2+) on PATH
        tests_required=False,
        fails_on=GODOT_ERRORS,
        tasks=(
            Task(
                "run_gut_tests",
                "Run the project's GUT unit tests (addons/gut) headless.",
                "godot --headless --path . -s addons/gut/gut_cmdln.gd -gdir=res://test -gexit",
                lambda root: (root / "addons/gut").is_dir(),
                timeout=600,
            ),
        ),
    ),
    Profile(
        "python-game",
        _any(*(_contains(f, "pygame") for f in ("requirements.txt", "pyproject.toml", "setup.py"))),
        _python_game_verify,  # its tests if it has some, else every file compiles
        tests_required=False,
    ),
    Profile("gradle", lambda root: (root / "gradlew").is_file(), f"{GRADLE} build --console=plain -q"),
]


def detect(root: Path) -> Profile | None:
    return next((p for p in PROFILES if p.matches(root)), None)


def verify_command(profile: Profile, root: Path) -> str:
    return profile.verify(root) if callable(profile.verify) else profile.verify


GRADLE_START = re.compile(r"^(?:\.[\\/])?gradlew(?:\.bat)?(?=\s|$)")
TOOL_NAMES = {"verify", "check_files", "read_game_log", "search_sources", "read_source"}


def own_command(root: Path, command: str) -> str:
    """A verify command the model wrote, made to run here: the Gradle wrapper however it is spelled (the Windows
    shell finds neither gradlew.bat nor ./gradlew in the project folder), and "" (the profile's own command) for a
    tool name given as a command (check_files)."""
    profile = detect(root)
    if not profile or not command:
        return command
    if command.split()[0] in TOOL_NAMES | {t.tool for t in profile.tasks}:
        return ""
    return GRADLE_START.sub(GRADLE.replace("\\", "\\\\"), command)


def failed_anyway(root: Path, output: str) -> bool:
    """A command that exited with 0 but printed what the profile counts as an error (Godot's script errors)."""
    profile = detect(root)
    return bool(profile and profile.fails_on and re.search(profile.fails_on, output))


def available_tasks(root: Path) -> list[Task]:
    profile = detect(root)
    return [t for t in (profile.tasks if profile else ()) if t.available(root)]


def run_task(root: Path, task: Task) -> str:
    from local_ai.coder import verify as vf

    result = vf.run(task.command, root, task.timeout)
    passed = result.passed and not failed_anyway(root, result.output)
    state = "PASSED" if passed else "FAILED"
    lines = result.output.splitlines()  # a game server logs a lot: its errors and the summary are what counts
    kept = _unique(_problems(lines))[-80:] + [t for t in lines if TASK_SUMMARY.search(t)][-10:]
    return f"{state} in {result.seconds:.0f} s: {task.command}\n{vf.summarize(chr(10).join(kept) or result.output)}"


TASK_SUMMARY = re.compile(r"tests? (?:passed|failed)|TESTS COMPLETE|BUILD (?:SUCCESSFUL|FAILED)|FAILED|Timed out")


def source_archives(root: Path) -> list[Path]:
    profile = detect(root)
    found = sorted(a for glob in (profile.sources if profile else ()) for a in root.glob(glob))
    return list({a.name: a for a in reversed(found)}.values())  # each module keeps a copy of the same jar: one


def search_sources(root: Path, pattern: str, glob: str = "") -> str:
    """Lines of the library sources that match: where a class or method is declared and how it is written."""
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))
    hits, total = [], 0
    for archive in source_archives(root):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.endswith("/") or (glob and not fnmatch.fnmatch(name.rsplit("/", 1)[-1], glob)):
                    continue
                for number, line in enumerate(z.read(name).decode("utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        total += 1
                        if len(hits) < MAX_HITS:
                            hits.append(f"{name}:{number}: {line.strip()[:200]}")
    if not hits:
        return "No matches in the library sources. Try a shorter pattern, e.g. a class or method name."
    more = f"\n[{total - MAX_HITS} more matches; narrow the pattern or give glob]" if total > MAX_HITS else ""
    return f"{total} matches. Read one with read_source(path, start, end).\n" + "\n".join(hits) + more


def read_source(root: Path, path: str, start: int = 1, end: int = 0) -> str:
    """Lines start..end of a library source file, as search_sources names it."""
    for archive in source_archives(root):
        with zipfile.ZipFile(archive) as z:
            if path in z.namelist():
                lines = z.read(path).decode("utf-8", errors="replace").splitlines()
                start = max(start, 1)
                chunk = "\n".join(f"{n}: {t}" for n, t in enumerate(lines[start - 1 : end or None], start))
                cut = f"\n[cut; {len(lines)} lines in all, read on with start/end]" if len(chunk) > MAX_READ else ""
                return chunk[:MAX_READ] + cut
    return f"Not found: {path}. Pass a path exactly as search_sources shows it."


MAX_CHECK = 40  # lines of one check report


def check_files(root: Path) -> tuple[list[str], list[str]] | None:
    """The profile's check of the files the compiler does not see, or None when it has none."""
    profile = detect(root)
    return profile.check(root) if profile and profile.check else None


def check_report(errors: list[str], warnings: list[str]) -> str:
    if not errors and not warnings:
        return "Resource files OK."
    lines = [f"{len(errors)} errors, {len(warnings)} warnings in the resource files:"]
    lines += [f"ERROR {e}" for e in errors] + [f"warning {w}" for w in warnings]
    more = f"\n[{len(lines) - 1 - MAX_CHECK} more]" if len(lines) - 1 > MAX_CHECK else ""
    return "\n".join(lines[: MAX_CHECK + 1]) + more


LOG_PROBLEM = re.compile(r"/(?:ERROR|FATAL)\]|Exception|Caused by:|\bError\b")
MAX_LOG = 8000  # characters returned by one read_game_log


def log_files(root: Path) -> list[Path]:
    """The logs and crash reports of the project's last runs, newest first."""
    profile = detect(root)
    found = {p for glob in (profile.logs if profile else ()) for p in root.glob(glob) if p.is_file()}
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _problems(lines: list[str]) -> list[str]:
    """Error lines of a log, each with the first lines of its stack trace."""
    kept: list[str] = []
    trace = 0
    for line in lines:
        if LOG_PROBLEM.search(line):
            kept.append(line.rstrip())
            trace = 6
        elif trace and line.lstrip().startswith(("at ", "Caused by", "...")):
            kept.append(line.rstrip())
            trace -= 1
        else:
            trace = 0
    return kept


def _unique(lines: list[str]) -> list[str]:
    """The same message (numbers aside) once, with how often it came."""
    counts: dict[str, list] = {}
    for line in lines:
        counts.setdefault(re.sub(r"\d+", "N", line.split("]: ", 1)[-1]), [line, 0])[1] += 1
    return [line + (f"  [x{n}]" if n > 1 else "") for line, n in counts.values()]


def read_game_log(root: Path) -> str:
    """What went wrong in the last run: the newest crash report, or the errors of the newest log."""
    files = log_files(root)
    if not files:
        return "No log or crash report yet: the user has to run the game (or program) once, then call this again."
    newest = files[0]
    age = int((time.time() - newest.stat().st_mtime) / 60)
    lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
    head = f"{newest.relative_to(root).as_posix()} (written {age} min ago)\n"
    if "crash-reports" in newest.parts:
        end = next((i for i, t in enumerate(lines) if t.startswith("A detailed walkthrough")), 80)
        body = "\n".join(lines[:end]).strip()
    else:
        problems = _problems(lines)
        if not problems:
            return head + "No errors in this log."
        unique = _unique(problems)
        body = f"{len(problems)} error lines, {len(unique)} different, the last ones:\n" + "\n".join(unique[-120:])
    return head + (body if len(body) <= MAX_LOG else body[: MAX_LOG // 2] + "\n[...]\n" + body[-MAX_LOG // 2 :])

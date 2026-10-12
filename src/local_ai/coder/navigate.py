"""Finding the way in a project that does not fit into the context: a search, a map of what the files define and
the outline of one file. The model reads the few places a task touches, not the files from top to bottom.

Python files are parsed (classes, functions, signatures, line ranges); for other languages the lines that start a
definition are recognised by their keyword.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder.verify import CODE_SUFFIXES

MAX_FILE = 1_000_000  # bytes; larger files are generated or data, not something to search or map
MAX_HITS = 60  # matches shown by one search
MAX_MAP = 11_000  # characters of one repo_map reply (the agent keeps 12000 of it)
TASK_MAP = 6_000  # ... of the map that is given with the task
LARGE_PROJECT = 12  # source files; from here on the map and the existing-code skill come with the task
SIGNATURE = 70  # characters of a function's parameters shown in the map
DEFINITION = re.compile(
    r"^(\s*)(?:export\s+|default\s+|public\s+|private\s+|protected\s+|static\s+|async\s+|pub(?:\([a-z]+\))?\s+|abstract\s+)*"
    r"(function|class|def|fn|func|struct|interface|enum|trait|impl|type|module)\s+([A-Za-z_$][\w$]*)"
)
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Words of a task that say nothing about where in the project it is.
COMMON = set(
    "the and for with that this from into when then make add fix bug error test tests code file files function class "
    "method should must not does use using new change update remove implement return value values all any".split()
)


@dataclass
class Definition:
    kind: str  # class, def, fn, ...
    name: str
    detail: str  # the parameters of a function, the bases of a class
    start: int
    end: int | None  # None when the language is not parsed
    depth: int  # 0 at the top of the file, 1 inside a class


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.split(".")[0].endswith("_test") or "/tests/" in f"/{path}"


def source_files(root: Path, base: Path | None = None) -> list[str]:
    """The source files under base (default: the project), tests last."""
    names = [n for n in fl.walk(root, base) if not n.endswith("/") and Path(n).suffix.lower() in CODE_SUFFIXES]
    return sorted(names, key=lambda n: (is_test(n), n))


def read_text(path: Path) -> str | None:
    """The text of a file, or None when it is too large, minified or not text."""
    try:
        if ".min." in path.name or path.stat().st_size > MAX_FILE:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _short(text: str, limit: int = SIGNATURE) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _python_definitions(text: str) -> list[Definition] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found: list[Definition] = []

    def add(node, depth: int) -> None:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            found.append(Definition("class", node.name, f"({_short(bases)})" if bases else "", node.lineno, node.end_lineno, depth))
            if depth == 0:
                for child in node.body:
                    add(child, 1)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(Definition("def", node.name, f"({_short(ast.unparse(node.args))})", node.lineno, node.end_lineno, depth))

    for node in tree.body:
        add(node, 0)
    return found


def definitions(path: str, text: str) -> list[Definition]:
    """What a file defines, in the order of the file."""
    if path.endswith(".py"):
        parsed = _python_definitions(text)
        if parsed is not None:
            return parsed
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        m = DEFINITION.match(line)
        if m:
            found.append(Definition(m.group(2), m.group(3), "", number, None, 1 if m.group(1) else 0))
    return found


def outline(root: Path, path: str) -> str:
    """The definitions of one file with their lines, to read only the part that is needed."""
    try:
        target = fl.resolve(root, path)
    except ValueError as e:
        return f"Error: {e}"
    text = read_text(target) if target.is_file() else None
    if text is None:
        return f"Error: {path} is not a text file of the project."
    found = definitions(path, text)
    total = len(text.splitlines())
    if not found:
        return f"{path} ({total} lines): no definitions found. Read it with read_file."
    lines = [f"{path} ({total} lines). Read a part with read_file(path, start, end)."]
    for d in found:
        span = f"{d.start}-{d.end}" if d.end else str(d.start)
        lines.append(f"{'  ' * d.depth}{span:>9}  {d.kind} {d.name}{d.detail}")
    return "\n".join(lines)


def search(root: Path, pattern: str, path: str = ".", glob: str = "", max_results: int = MAX_HITS) -> str:
    """Lines of the project that match a regular expression (plain text when it is not one), as file:line: text.
    A pattern in lower case ignores case. glob limits the search to file names like *.py."""
    if not pattern:
        return "Error: give a pattern to search for."
    try:
        base = fl.resolve(root, path)
    except ValueError as e:
        return f"Error: {e}"
    flags = re.IGNORECASE if pattern == pattern.lower() else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        regex = re.compile(re.escape(pattern), flags)
    if base.is_file():
        names = [base.relative_to(root).as_posix()]
    elif base.is_dir():
        names = [n for n in fl.walk(root, base) if not n.endswith("/")]
    else:
        return f"Error: {path} does not exist in the project."
    hits, total, files_with, searched = [], 0, 0, 0
    for name in names:
        if glob and not fnmatch(name.rsplit("/", 1)[-1], glob):
            continue
        text = read_text(root / name)
        if text is None:
            continue
        searched += 1
        before = total
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                total += 1
                if len(hits) < max_results:
                    hits.append(f"{name}:{number}: {_short(line.strip(), 200)}")
        files_with += total > before
    if not hits:
        return f"No matches for {pattern!r} in {searched} files. Try a shorter pattern or another name."
    more = f"\n[{total - len(hits)} more matches not shown; narrow the pattern or pass path or glob]" if total > len(hits) else ""
    return f"{total} matches in {files_with} files for {pattern!r}:\n" + "\n".join(hits) + more


# -- the map ----------------------------------------------------------------------------------------


def _file_entry(name: str, lines: int, found: list[Definition], level: int) -> str:
    """One file of the map. Level 0: every definition with its parameters; 1: names only; 2: the file alone."""
    head = f"{name} ({lines} lines)"
    if level == 2 or not found:
        return head
    top = [d for d in found if d.depth == 0]
    if level == 1:
        return f"{head}: " + _short(", ".join(d.name for d in top), 160)
    lines = [head]
    for d in top:
        if d.kind == "class":
            methods = [m.name for m in found if m.depth == 1 and d.start < m.start <= (d.end or 10**9)]
            lines.append(f"  class {d.name}{d.detail}" + (f": {_short(', '.join(methods), 120)}" if methods else ""))
        else:
            lines.append(f"  {d.kind} {d.name}{d.detail}")
    return "\n".join(lines)


def _relevance(name: str, found: list[Definition], terms: set[str]) -> int:
    """How much a file has to do with the words of a task: its path and the names it defines."""
    if not terms:
        return 0
    path = name.lower()
    names = {d.name.lower() for d in found}
    return sum(3 * (term in names) + (term in path) + any(term in n for n in names) for term in terms)


def repo_map(root: Path, path: str = ".", focus: str = "", budget: int = MAX_MAP) -> str:
    """The source files under path with what they define. As much detail as fits: parameters, then names only,
    then the files alone. focus: words of the task; the files they point to keep their detail and come first."""
    try:
        base = fl.resolve(root, path)
    except ValueError as e:
        return f"Error: {e}"
    if not base.is_dir():
        return f"Error: {path} is not a folder; use outline for a file."
    texts = {}
    for name in source_files(root, base):
        text = read_text(root / name)
        if text is not None:
            texts[name] = text
    if not texts:
        return f"No source files in {path}."
    lines = {name: len(text.splitlines()) for name, text in texts.items()}
    found = {name: definitions(name, text) for name, text in texts.items()}  # parsed once
    total_lines = sum(lines.values())
    head = (
        f"Map of {path} ({len(texts)} source files, {total_lines} lines). More detail: repo_map(path=<folder>), "
        "outline(<file>); where a name is used: search(<name>)."
    )
    terms = {w.lower() for w in WORD.findall(focus)} - COMMON
    scores = {name: _relevance(name, found[name], terms) for name in texts}
    order = sorted(texts, key=lambda n: (-scores[n], is_test(n), n))
    related = [n for n in order if scores[n] > 0]
    # the files the task points to in full detail, as far as half of the room goes
    entries, used = {}, len(head)
    for name in related:
        entry = _file_entry(name, lines[name], found[name], 0)
        if used + len(entry) > budget // 2:
            break
        entries[name] = entry
        used += len(entry) + 1
    rest = [n for n in order if n not in entries]
    for level in (0, 1, 2):
        rendered = [_file_entry(n, lines[n], found[n], level) for n in rest]
        if used + sum(len(r) + 1 for r in rendered) <= budget:
            break
    else:  # too many files even by name: the folders with their size
        folders: dict[str, list[int]] = {}
        for n in rest:
            folder = n.rsplit("/", 1)[0] if "/" in n else "."
            count = folders.setdefault(folder, [0, 0])
            count[0] += 1
            count[1] += lines[n]
        rendered = [f"{folder}/ ({count[0]} files, {count[1]} lines)" for folder, count in sorted(folders.items())]
    out = [head]
    if entries:
        out += ["", "Closest to the task:", *entries.values(), "", "The rest:"]
    return "\n".join([*out, *rendered])[: budget + 200]


def task_map(root: Path, task: str) -> str:
    """The map that comes with a task, or "" for a project small enough to look at with list_files."""
    if len(source_files(root)) < LARGE_PROJECT:
        return ""
    return repo_map(root, ".", focus=task, budget=TASK_MAP)

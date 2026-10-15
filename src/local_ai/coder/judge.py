"""The judge: a check of a finished task that does not share the coder's view of it.

The coder's own tests can only check what the coder understood; when it misread the task, its tests misread it the
same way. So before a task is called done the program runs the code examples of the task itself (an issue usually
shows what to run and what goes wrong) against the changed project, and a fresh conversation, which sees only the
task, the diff and those outputs, decides whether the task is really solved.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from local_ai.coder import verify as vf
from local_ai.core.llm import LLM
from local_ai.core.text import strip_think, trim

MAX_SNIPPETS = 4
SNIPPET_TIMEOUT = 60
OUTPUT_CHARS = 2500  # of one example's output
DIFF_CHARS = 12_000
FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
SYSTEM = (
    "You are a strict reviewer of a code change. You get a task (often a GitHub issue), the change that was made "
    "for it (a git diff) and the output of the task's own examples run on the changed code. Decide whether the "
    "change really solves the task: the behaviour the task asks for, in every case it names or clearly implies, "
    "not only the first example. A change that only hides the symptom, special-cases the example, or leaves a "
    "case of the task wrong does not solve it. Do not judge style.\n\n"
    "Answer in this form, in English:\n"
    "VERDICT: SOLVED or VERDICT: NOT SOLVED\n"
    "Then, if not solved, at most five short lines: what is still wrong, and which input shows it."
)
REQUEST = "TASK:\n{task}\n\nTHE CHANGE (git diff):\n{diff}\n\nTHE TASK'S EXAMPLES ON THE CHANGED CODE:\n{examples}"


@dataclass
class Verdict:
    solved: bool
    reasons: str  # what is still wrong ("" when solved)
    examples: str  # the examples and their outputs, as shown to the judge


def parses(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def snippets(task: str) -> list[str]:
    """The Python examples of a task: its fenced code blocks, with the prompts of an interactive session (>>>,
    In [1]:) removed and the session's printed output dropped. A block that mixes code with its output (Out[]:,
    a traceback) or ends in Python 2 is cut to the longest start that is Python; blocks without any are left out,
    and so is a block that is a single value without an import: printed output (a list, a string) of the issue."""
    found = []
    for block in FENCE.findall(task):
        lines = block.splitlines()
        session = any(line.lstrip().startswith((">>>", "In [")) for line in lines)
        if session:
            kept = []
            for line in lines:
                stripped = line.lstrip()
                prompt = re.match(r"(>>>|\.\.\.|In \[\d*\]:|\s*\.\.\.:)( |$)", stripped)
                if prompt:
                    kept.append(stripped[prompt.end() :])
            lines = kept
        while lines and not parses("\n".join(lines)):
            lines = lines[:-1]
        code = "\n".join(lines).strip()
        if not code or code.startswith("Traceback"):
            continue
        body = ast.parse(code).body
        if not session and "import " not in code and len(body) == 1 and isinstance(body[0], ast.Expr):
            continue
        if code not in found:  # an issue may repeat itself
            found.append(code)
    return found[:MAX_SNIPPETS]


def run_examples(task: str, root: Path) -> str:
    """The examples of the task run one after the other in the project's environment, in one scope (a later
    example often goes on with the names of an earlier one), each with its output; "" when the task has none."""
    codes = snippets(task)
    if not codes:
        return ""
    result = vf.run_script(REPL_RUNNER.format(codes=codes, marker=MARKER), root, SNIPPET_TIMEOUT * len(codes))
    outputs = result.output.split(MARKER)[1:] or [result.output]  # no marker: it failed before the first one
    parts = []
    for i, code in enumerate(codes, 1):
        output = (outputs[i - 1] if i <= len(outputs) else "(not reached)").strip() or "(no output)"
        parts.append(f"Example {i}:\n```python\n{code}\n```\nOutput:\n```\n{trim(output, OUTPUT_CHARS)}\n```")
    return "\n\n".join(parts)


MARKER = "--- local-ai example ---"  # printed on a line of its own before each example
# Runs the examples like the interactive interpreter: the value of an expression on its own line is printed, and an
# error is printed while the next statement still runs (an issue often shows several lines that each fail).
REPL_RUNNER = """
import ast, sys, traceback
scope = {{"__name__": "__main__"}}
for source in {codes!r}:
    print("\\n" + {marker!r})
    for node in ast.parse(source).body:
        try:
            if isinstance(node, ast.Expr):
                code = compile(ast.Interactive([node]), "<example>", "single")
            else:
                code = compile(ast.Module([node], []), "<example>", "exec")
            exec(code, scope)
        except Exception:
            print("\\n".join(traceback.format_exc().splitlines()[-6:]))
        sys.stdout.flush()
"""


def diff(root: Path) -> str:
    """The change against the last commit with the new files; "" when the folder is not a git repository. The
    index is not touched: what the user staged stays staged."""

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace")
        return done.stdout if done.returncode == 0 else ""

    if not git("rev-parse", "--verify", "HEAD"):
        return ""
    parts = [git("diff", "HEAD", "--", ".", ":(exclude).local-coder").strip()]
    for name in git("ls-files", "--others", "--exclude-standard").splitlines()[:20]:
        path = root / name
        if name.startswith(".local-coder") or not path.is_file() or path.stat().st_size > 50_000:
            continue
        parts.append(f"New file {name}:\n{path.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(p for p in parts if p)


def parse(text: str) -> tuple[bool, str] | None:
    """(solved, reasons) from the judge's answer; None when it gave no verdict."""
    match = re.search(r"VERDICT:\s*\**\s*(NOT\s+SOLVED|SOLVED)", text, re.I)
    if not match:
        return None
    solved = not match.group(1).upper().startswith("NOT")
    reasons = text[match.end() :].strip().strip("*").strip()
    return solved, "" if solved else reasons[:1500]


async def judge(llm: LLM, task: str, root: Path, max_tokens: int = 8192) -> Verdict | None:
    """The judge's verdict on the change in root; None when there is nothing to judge (no git history, no
    change) or the judge gave no clear answer: then the task is not held up."""
    change = diff(root)
    if not change:
        return None
    examples = run_examples(task, root) or "(the task has no code examples to run)"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": REQUEST.format(task=task, diff=trim(change, DIFF_CHARS), examples=examples)},
    ]
    reply = await llm.complete(messages, None, max_tokens=max_tokens)
    found = parse(strip_think(reply.content)) if reply.finish_reason != "length" else None
    if found is None:
        return None
    return Verdict(found[0], found[1], examples)

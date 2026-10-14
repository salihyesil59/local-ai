"""The agent's tools, called in-process: the same functions the MCP server offers, with the project folder fixed."""

from __future__ import annotations

from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import navigate as nav
from local_ai.coder import skills as sk
from local_ai.coder import state as st
from local_ai.coder import verify as vf
from local_ai.coder import workflow as wf
from local_ai.core.tools import Tool

NO_PLAN_YET = "Error: there is no plan yet. Call plan first; then edit files."
VERIFY_TIMEOUT = 900  # seconds for one verify: the whole test suite of a large project
SCRIPT_TIMEOUT = 60  # seconds for one run_python
EXISTING_TESTS = (
    "Error: not changed. This would change or delete tests that were in {path} before: {tests}. An existing test "
    "that fails after your fix means the fix broke behaviour that was right: change the fix so that the test "
    "passes as it is. Add your own tests next to the existing ones, with edit_file. Only if the task itself asks "
    "for the behaviour these tests check to change, make the same call again and say why in the verify note."
)


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


STR = {"type": "string"}
STRS = {"type": "array", "items": {"type": "string"}}
INT = {"type": "integer"}

EDIT_TOOLS = ("write_file", "edit_file")


def coder_tools(root: Path, require_skill: bool = True) -> dict[str, Tool]:
    seen = {"skill": not require_skill}  # a skill has been read in this run
    originals: dict[Path, str | None] = {}  # file -> its text before this run first changed it (None: it was new)
    warned: set[Path] = set()  # test files whose existing tests the model was told to keep

    def original(path: str) -> str | None:
        try:
            target = fl.resolve(root, path)
        except ValueError:
            return None
        if target not in originals:
            try:
                originals[target] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                originals[target] = None
        return originals[target]

    def keeps_tests(path: str, removed: set[str]) -> str | None:
        """Why a change to the existing tests of a file is refused the first time; None when it is not."""
        before = original(path)
        if before is None or not fl.is_test_file(path) or fl.resolve(root, path) in warned:
            return None
        lines = fl.test_lines(before)
        tests = sorted({lines[line] for line in removed if line in lines})
        if not tests:
            return None
        warned.add(fl.resolve(root, path))
        return EXISTING_TESTS.format(path=path, tests=", ".join(tests[:10]))

    def plan(goal: str, steps: list[str], criteria: list[str], verify_command: str = "") -> str:
        steps = [steps] if isinstance(steps, str) else steps
        criteria = [criteria] if isinstance(criteria, str) else criteria
        skills = sk.load_skills()
        if not seen["skill"] and skills and st.load(root) is None:
            # The model does not load skills by itself: the program picks the one that fits and hands it over.
            name = sk.suggest(skills, " ".join([goal, *steps, *criteria]))
            seen["skill"] = True
            return (
                f"Error: the plan was not saved. Before planning, read the skill for this kind of task ({name}) "
                f"and plan according to it, then call plan again:\n\n{skills[name]['body']}"
            )
        return wf.make_plan(root, goal, steps, criteria, verify_command)

    def verify(note: str = "", tests: str = "") -> str:
        return wf.run_verify(root, note, VERIFY_TIMEOUT, tests)

    def run_python(code: str) -> str:
        result = vf.run_script(code, root, SCRIPT_TIMEOUT)
        return ("" if result.passed else "The script failed.\n") + vf.summarize(result.output)

    def search(pattern: str, path: str = ".", glob: str = "") -> str:
        return nav.search(root, pattern, path, glob)

    def repo_map(path: str = ".") -> str:
        return nav.repo_map(root, path)

    def outline(path: str) -> str:
        return nav.outline(root, path)

    def status() -> str:
        return wf.show_status(root)

    def write_file(path: str, content: str) -> str:
        if st.load(root) is None:
            return NO_PLAN_YET
        before = original(path)
        if before is not None:
            kept = {line.strip() for line in content.splitlines()}
            refused = keeps_tests(path, {line.strip() for line in before.splitlines()} - kept)
            if refused:
                return refused
        return fl.write_file(root, path, content)

    def edit_file(path: str, old: str, new: str) -> str:
        if st.load(root) is None:
            return NO_PLAN_YET
        original(path)
        kept = {line.strip() for line in new.splitlines()}
        refused = keeps_tests(path, {line.strip() for line in old.splitlines()} - kept)
        return refused or fl.edit_file(root, path, old, new)

    def load_skill(name: str) -> str:
        found = sk.load_skills()
        skill = found.get(name.strip())
        seen["skill"] = True
        if skill is None:
            return f"Error: unknown skill {name!r}. Skills: {', '.join(found)}"
        return f"Skill {skill['name']}:\n{skill['body']}"

    def read_file(path: str, start: int = 1, end: int = 0) -> str:
        return fl.read_file(root, path, start, end)

    def list_files(path: str = ".") -> str:
        return fl.list_files(root, path)

    tools = [
        Tool(
            "plan",
            "Write the plan for the task. Call this first; calling it again replaces the plan. steps: small changes to "
            "the code, each including its tests (never a step like 'write the tests'). criteria: every requirement and "
            "example of the request with its exact expected result, one per item. Leave verify_command empty.",
            _schema(
                {"goal": STR, "steps": STRS, "criteria": STRS, "verify_command": STR}, ["goal", "steps", "criteria"]
            ),
            plan,
        ),
        Tool(
            "verify",
            "Run the tests for the current step. PASSED gives the next step (or the review); FAILED gives the failure "
            "to fix. note: one line about what you did, or in the review what you added. tests: in a large project "
            "the test files of the step ('tests/test_x.py'), only they run; the review always runs everything.",
            _schema({"note": STR, "tests": STR}, []),
            verify,
        ),
        Tool(
            "run_python",
            "Run a short Python script in the project folder, with the project's packages, and get what it prints. "
            "Use it to SEE what the code does: print the real output for the input of a bug report, the value of "
            "an expression, the result of a call. Do not work such things out in your head. It checks nothing: "
            "the task is done only through verify.",
            _schema({"code": STR}, ["code"]),
            run_python,
        ),
        Tool(
            "load_skill",
            "Load the procedure of a skill (see the skill list in the instructions) and follow it.",
            _schema({"name": STR}, ["name"]),
            load_skill,
        ),
        Tool("status", "Show the plan, the current step and the last verification result.", _schema({}, []), status),
        Tool(
            "read_file",
            "Read a text file of the project. start/end are 1-based line numbers (end 0 = to the end).",
            _schema({"path": STR, "start": INT, "end": INT}, ["path"]),
            read_file,
        ),
        Tool(
            "write_file",
            "Create a file or replace it completely; missing folders are created. Prefer edit_file for changes.",
            _schema({"path": STR, "content": STR}, ["path", "content"]),
            write_file,
        ),
        Tool(
            "edit_file",
            "Replace one exact piece of text in a file. old must appear exactly once: copy it from read_file.",
            _schema({"path": STR, "old": STR, "new": STR}, ["path", "old", "new"]),
            edit_file,
        ),
        Tool(
            "list_files",
            "List the files under a folder of the project (caches and virtual environments are skipped).",
            _schema({"path": STR}, []),
            list_files,
        ),
        Tool(
            "search",
            "Find lines in the project: where a name is defined and where it is used. pattern is a regular "
            "expression or plain text (lower case ignores case). path: a folder or file; glob: file names like "
            "'*.py'. Returns file:line: text.",
            _schema({"pattern": STR, "path": STR, "glob": STR}, ["pattern"]),
            search,
        ),
        Tool(
            "repo_map",
            "Map of the project or of one folder: its source files with the classes and functions they define.",
            _schema({"path": STR}, []),
            repo_map,
        ),
        Tool(
            "outline",
            "What one file defines, with line numbers. Use it before reading a long file, then read only the part "
            "you need with read_file(path, start, end).",
            _schema({"path": STR}, ["path"]),
            outline,
        ),
    ]
    return {t.name: t for t in tools}

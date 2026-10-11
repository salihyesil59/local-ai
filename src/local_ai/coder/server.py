"""Coder MCP server: the plan → implement → verify → fix → review loop for a coding agent.

The loop's state lives here, not in the model's head. Every tool result says what to do next, and the only way
to finish is a verification that really passes, followed by a review of the acceptance criteria.

Tools:
  plan(goal, steps, criteria, verify_command, project)   write the plan; returns the first step
  verify(note, timeout)                                  run the tests; next step on success, else the failure
  status()                                               plan and progress, to resume after a compaction or retry

Not a sandbox: the verify command runs as your user in the project folder.

Run:  local-coder
"""

from __future__ import annotations

import re
from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import state as st
from local_ai.coder import verify as vf

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP

REPEAT_HINT = 3  # same failure this many times in a row: change the approach
REPEAT_STOP = 6  # ... this many times: stop and report to the user
# A step that is only about tests ("Write the tests", "Run all tests", "Test the edge cases"): tests belong to the
# step that writes the code they check. A step that reproduces a bug in a failing test is fine.
TEST_ONLY = re.compile(
    r"^(?:step\s*\d+\s*[:.\-]\s*)?(?:(?:write|add|create|run|execute|verify|make)\s+"
    r"(?:(?!with\b|and\b|for\b|its\b|their\b)[\w-]+\s+){0,3}?(?:tests?|test suite)\b|tests?\s)",
    re.IGNORECASE,
)
REPRODUCES = re.compile(r"reproduc|failing|regression|bug", re.IGNORECASE)

INSTRUCTIONS = (
    "Workflow for every coding task (new code, feature, bug fix, refactoring):\n"
    "1. Look at the existing code first, then call plan(goal, steps, criteria). Each step is one small change to "
    "the code and includes its tests; a small task has one step. Writing the tests or running them is not a "
    "step of its own. A bug fix starts with a step that reproduces the bug in a failing test. criteria lists "
    "every requirement and example of the user's request, one per item.\n"
    "2. Implement the current step and write its tests at the same time, before you check anything.\n"
    "3. Call verify to check your work. Do not run the tests yourself and do not debug with throwaway scripts "
    "or python -c: put what you want to check into a test and call verify. FAILED: read the output, fix the "
    "cause, call verify again. PASSED: it gives you the next step.\n"
    "4. After the last step verify asks for a review: check every criterion against the tests, add the missing "
    "tests, fix what they uncover, call verify again.\n"
    "5. The task is finished only when verify reports ALL DONE. Never tell the user it works before that. Never "
    "weaken, skip or delete a test to make verify pass; a test that accepts several different results is weakened.\n"
    "Call status when you are unsure where you are."
)

NO_PLAN = "No plan in {root}. Call plan(goal, steps, criteria) first (pass project if this is not the project folder)."
NO_COMMAND = (
    "CANNOT VERIFY: no test command found in {root}. Write tests for the current step first "
    "(Python: tests/test_*.py for pytest), or call plan again with verify_command."
)
ALL_DONE = "ALL DONE: the steps and the review are finished and verification passes."


def review_text(state: dict) -> str:
    criteria = "\n".join(f"  {i}. {c}" for i, c in enumerate(state["criteria"], 1))
    return (
        "REVIEW before you finish. Re-read the user's request and check every acceptance criterion against the "
        f"tests:\n{criteria}\n"
        "For each one, find the test that checks exactly that behaviour with the exact expected value; a test "
        "that accepts several different results does not count. Also test every error the request lists with one "
        "input that triggers it, including badly written input (malformed numbers, unknown names), and every form "
        "an argument may take (for example a number, a list or a tuple) on every function the request says accepts "
        "it; check that the result has the form the request names (an error value or an exception of that type). "
        "Write the missing tests, fix what they uncover, then call verify with a note that names the tests that "
        "cover the criteria (for example test_parse_dates in tests/test_dates.py)."
    )


def make_plan(root: Path, goal: str, steps: list[str], criteria: list[str], verify_command: str = "") -> str:
    steps = [s.strip() for s in steps if s.strip()]
    criteria = [c.strip() for c in criteria if c.strip()]
    if not goal.strip() or not steps or not criteria:
        return "Error: give a goal, at least one step and at least one acceptance criterion."
    if not root.is_dir():
        return f"Error: project folder {root} does not exist."
    for number, step in enumerate(steps, 1):
        if TEST_ONLY.match(step) and not REPRODUCES.search(step):
            return (
                f"Error: step {number} ({step!r}) is only about tests. Tests belong to the step that writes the code "
                "they check: every step is a change to the code and includes its tests. Remove the step or merge "
                "it into the step that implements what it tests, then call plan again."
            )
    state = st.new_state(goal.strip(), steps, criteria, verify_command.strip() or (vf.detect_command(root) or ""))
    state["snapshot"] = vf.snapshot(root)
    st.save(root, state)
    st.log_event(root, event="plan", goal=state["goal"], steps=len(steps), criteria=len(criteria))
    return (
        f"Plan saved ({len(steps)} steps, {len(criteria)} criteria).\n{st.render(root, state)}\n\n"
        f"Now implement step 1: {steps[0]}\nWrite its tests at the same time, then call verify."
    )


def run_verify(root: Path, note: str = "", timeout: float = 300) -> str:
    state = st.load(root)
    if state is None:
        return NO_PLAN.format(root=root)
    command = state["verify_command"] or vf.detect_command(root)
    if not command:
        return NO_COMMAND.format(root=root)
    state["verify_command"] = command
    total = len(state["steps"])
    index = st.current(state)
    where = f"step {index + 1}/{total}" if index is not None else "review"
    # Same files as at the plan or the last passing verify: the model has not worked since then.
    unchanged = state["snapshot"] == vf.snapshot(root)
    result = vf.run(command, root, timeout)
    took = f"{result.seconds:.0f} s"

    if result.passed:
        state.update(attempts=0, repeats=0, last={"passed": True, "line": "", "signature": ""})
        state["snapshot"] = vf.snapshot(root)  # taken after the run: tests may write files
        if st.finished(state):
            message = f"PASSED ({took}). The task was already finished and verification still passes."
        elif index is None:  # review
            if unchanged and not (note.strip() and vf.note_cites_project(note, root)):
                message = (
                    f"PASSED ({took}), but the review is still open: no file has changed and the note names no file "
                    f"or test of the project.\n{review_text(state)}"
                )
            else:
                state["review"] = "done"
                if note.strip():
                    state["review_note"] = note.strip()
                message = f"PASSED (review, {took}).\n{ALL_DONE} Give the user a short summary of what changed."
        elif unchanged and not (note.strip() and vf.note_cites_project(note, root)):
            message = (
                f"PASSED ({took}), but no file has changed since the plan or the last passing verify, and the note "
                f"names no file or test of the project, so step {index + 1}/{total} stays open: "
                f"{state['steps'][index]['text']}\n"
                "Implement it with its tests, then call verify. If your earlier changes already cover this step, "
                "call verify with a note that names the file that implements it and the test that covers it "
                "(for example engine.py and test_cycle in tests/test_engine.py)."
            )
        else:
            step = state["steps"][index]
            step["done"] = True
            if note.strip():
                step["note"] = note.strip()
            head = f"PASSED (step {index + 1}/{total}, {took}). Step {index + 1} is done."
            if index + 1 < total:
                message = (
                    f"{head}\nNext step {index + 2}/{total}: {state['steps'][index + 1]['text']}\n"
                    "Implement it and write its tests at the same time, then call verify."
                )
            else:
                state["review"] = "open"
                message = f"{head} All {total} steps are implemented.\n{review_text(state)}"
    else:
        state["review"] = "open" if index is None else state["review"]  # a finished task that broke is reviewed again
        sig = vf.signature(result.output)
        same = state["last"] is not None and not state["last"]["passed"] and state["last"]["signature"] == sig
        state["repeats"] = state["repeats"] + 1 if same else 1
        state["attempts"] += 1
        state["last"] = {"passed": False, "line": vf.first_error(result.output), "signature": sig}
        if state["repeats"] >= REPEAT_STOP:
            advice = (
                f"The same failure has happened {state['repeats']} times in a row. Stop editing. Tell the user "
                "what the failure is, what you tried and what you think blocks it."
            )
        elif state["repeats"] >= REPEAT_HINT:
            advice = (
                f"The same failure has happened {state['repeats']} times in a row. Do not retry the same idea: "
                "re-read the failing test and the code it runs, state a different hypothesis, then fix that."
            )
        else:
            advice = "Fix the cause (the code, not the test's expectation), then call verify again."
        message = (
            f"FAILED ({where}, attempt {state['attempts']}, {took}): {command}\n"
            f"{vf.summarize(result.output)}\n\n{advice}"
        )

    st.save(root, state)
    st.log_event(
        root,
        event="verify",
        step=(index + 1 if index is not None else "review"),
        passed=result.passed,
        seconds=round(result.seconds, 1),
        attempt=state["attempts"],
        repeats=state["repeats"],
        unchanged=unchanged,
        done=st.finished(state),
    )
    return message


def show_status(root: Path) -> str:
    state = st.load(root)
    if state is None:
        return NO_PLAN.format(root=root)
    index = st.current(state)
    if index is not None:
        return f"{st.render(root, state)}\n\nImplement step {index + 1}, then call verify."
    if st.finished(state):
        return f"{st.render(root, state)}\n\n{ALL_DONE}"
    return f"{st.render(root, state)}\n\n{review_text(state)}"


def check(root: Path, timeout: float = 600) -> tuple[bool, str]:
    """For `local-coder check`: True only when the task is finished and the verify command passes right now."""
    state = st.load(root)
    if state is None:
        return False, NO_PLAN.format(root=root)
    if not st.finished(state):
        index = st.current(state)
        what = f"step {index + 1}/{len(state['steps'])}" if index is not None else "the review"
        return False, f"Not finished: {what} is open.\n{st.render(root, state)}"
    result = vf.run(state["verify_command"], root, timeout)
    if not result.passed:
        return False, f"Verification fails: {state['verify_command']}\n{vf.summarize(result.output)}"
    return True, ALL_DONE


FILES_INSTRUCTIONS = (
    "\nFile tools (read_file, write_file, edit_file, list_files) work inside the project folder; paths are "
    "relative to it. There is no shell: tests run only through verify. Always pass the project argument (absolute "
    "folder path) to plan, since this server does not know which folder you work in."
)


def build_server(files: bool = False) -> FastMCP:
    mcp = FastMCP("coder", instructions=INSTRUCTIONS + (FILES_INSTRUCTIONS if files else ""))
    root = st.default_root()  # replaced when plan is called with a project folder

    @mcp.tool()
    def plan(goal: str, steps: list[str], criteria: list[str], verify_command: str = "", project: str = "") -> str:
        """Write the plan for a coding task. Call this before editing code; calling it again replaces the plan.

        goal: what the user wants, in one or two sentences.
        steps: ordered small changes to the code, e.g.
          ["Add parse_date() for ISO dates", "Reject invalid dates with ValueError", ...].
          Every step includes its tests. Do not add steps like "write the tests" or "run the tests".
        criteria: the acceptance criteria, one per item: every requirement and every example of the user's
          request with its exact expected result, e.g. ["parse_date('2024-01-31') == date(2024, 1, 31)", ...].
          They are checked against the tests in the review at the end.
        verify_command: leave empty; the project's test command is detected (pytest, npm test, cargo test,
          go test). Set it only when the project needs another command that must exit 0 (build, linter, ...).
        project: absolute path of the project folder; leave empty for the current working directory.
        """
        nonlocal root
        if project.strip():
            root = Path(project.strip()).expanduser().resolve()
        return make_plan(root, goal, steps, criteria, verify_command)

    @mcp.tool()
    def verify(note: str = "", timeout: float = 300) -> str:
        """Run the verify command for the current step. Call it after every change.

        PASSED marks the step done and returns the next one, or the review after the last step.
        FAILED returns the failure to fix.
        note: one line about what you did in this step; in the review, what you added. timeout: seconds, at most 1800.
        """
        return run_verify(root, note, max(5.0, min(float(timeout), 1800.0)))

    @mcp.tool()
    def status() -> str:
        """Show the plan: goal, steps, criteria, the current step and the last verification result."""
        return show_status(root)

    if files:

        @mcp.tool()
        def read_file(path: str, start: int = 1, end: int = 0) -> str:
            """Read a text file of the project. start/end are 1-based line numbers (end 0 = to the end)."""
            return fl.read_file(root, path, start, end)

        @mcp.tool()
        def write_file(path: str, content: str) -> str:
            """Create a file or replace it completely; missing folders are created. Prefer edit_file for changes."""
            return fl.write_file(root, path, content)

        @mcp.tool()
        def edit_file(path: str, old: str, new: str) -> str:
            """Replace one exact piece of text in a file. old must appear exactly once: copy it from read_file."""
            return fl.edit_file(root, path, old, new)

        @mcp.tool()
        def list_files(path: str = ".") -> str:
            """List the files under a folder of the project (caches and virtual environments are skipped)."""
            return fl.list_files(root, path)

    return mcp


def main(files: bool = False) -> None:
    # A new session starts without a task; plan and verify write the reminder again.
    st.moim_file().write_text(st.IDLE, encoding="utf-8")
    build_server(files).run()


if __name__ == "__main__":
    main()

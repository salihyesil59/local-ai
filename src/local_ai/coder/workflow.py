"""The plan → implement → verify → fix → review loop of a coding task.

The loop's state lives here (and on disk, see state.py), not in the model's head. Every result says what to do
next, and the only way to finish is a verification that really passes, followed by a review of the acceptance
criteria. The MCP server (local_ai.servers.coder) and the coding agent's own tools (tools.py) call these functions.

Not a sandbox: the verify command runs as your user in the project folder.
"""

from __future__ import annotations

import re
from pathlib import Path

from local_ai.coder import state as st
from local_ai.coder import verify as vf

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

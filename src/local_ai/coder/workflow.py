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
BASELINE_TIMEOUT = 900  # seconds for the run that finds the tests failing before the work begins
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
    "In a large project give verify the tests of the step (tests=\"tests/test_x.py\"): only they run; the review "
    "at the end runs everything. Tests that already failed when the plan was saved are not counted.\n"
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
        "Every function the request asks for also needs one test that compares its result with a number or value "
        "that does not come from your own code: from the request, from the source you implement, a closed form, "
        "or a calculation by hand written in the test. A relation between two of your functions, a value at zero "
        "or an error case alone does not count: a wrong constant or unit passes all of them. "
        "Write the missing tests, fix what they uncover, then call verify with a note that names the tests that "
        "cover the criteria (for example test_parse_dates in tests/test_dates.py)."
    )


def make_plan(
    root: Path, goal: str, steps: list[str], criteria: list[str], verify_command: str = "", review: bool = True
) -> str:
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
    chosen = vf.in_project_env(verify_command.strip(), root)
    state = st.new_state(goal.strip(), steps, criteria, chosen or vf.detect_command(root) or "", review)
    # A command chosen for the task names the tests of the task: none of their failures is somebody else's.
    before = "" if chosen else failing_before(root, state)
    state["snapshot"] = vf.snapshot(root)  # after the run: tests may write files
    st.save(root, state)
    st.log_event(root, event="plan", goal=state["goal"], steps=len(steps), criteria=len(criteria))
    return (
        f"Plan saved ({len(steps)} steps, {len(criteria)} criteria).\n{st.render(root, state)}{before}\n\n"
        f"Now implement step 1: {steps[0]}\nWrite its tests at the same time, then call verify."
    )


def failing_before(root: Path, state: dict) -> str:
    """Run the project's whole test command once before the work begins. Tests that fail already, and that the
    plan does not name, go into state["baseline"]: verify does not count them, and says so every time.
    Returns what to tell the model about it."""
    command = state["verify_command"]
    if not command or not vf.has_tests(root):
        return ""
    full = vf.every_failure(command)
    result = vf.run(full, root, BASELINE_TIMEOUT)
    if result.timed_out:
        return (
            f"\n\nThe tests did not finish in {BASELINE_TIMEOUT} s. Give verify the tests of each step, for "
            'example verify(tests="tests/test_module.py").'
        )
    asked = " ".join([state["goal"], *(s["text"] for s in state["steps"]), *state["criteria"]]).lower()
    old = [] if result.passed else [t for t in vf.failed_tests(result.output) if not vf.test_mentioned(t, asked)]
    if not old:
        return ""
    state["baseline"] = old
    state["verify_command"] = full  # every failure must be reported, to tell the old ones from new ones
    shown = ", ".join(old[:8]) + (f" and {len(old) - 8} more" if len(old) > 8 else "")
    return (
        f"\n\n{len(old)} tests fail already, before your changes: {shown}. They are not counted against you; "
        "leave them alone unless the task is to fix them."
    )


def run_tests(state: dict, root: Path, command: str, timeout: float) -> tuple[bool, vf.Result, list[str]]:
    """Run the command. Passed also when the only failures are tests that failed before the work began:
    (passed, result, those tests)."""
    result = vf.run(command, root, timeout)
    if result.passed or result.timed_out or not state.get("baseline"):
        return result.passed, result, []
    failing = vf.failed_tests(result.output)
    if failing and set(failing) <= set(state["baseline"]):
        return True, result, failing
    return False, result, []


def run_verify(root: Path, note: str = "", timeout: float = 300, tests: str = "") -> str:
    """Run the verify command and move the plan on. tests: only these tests for a step (the review runs all)."""
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
    run_command, scope = command, ""
    if tests.strip() and index is not None:
        try:
            run_command = vf.focused(command, tests, root)
            scope = f", only {tests.strip()}"
        except ValueError as e:
            scope = f", all tests because {e}"
    passed, result, ignored = run_tests(state, root, run_command, timeout)
    took = f"{result.seconds:.0f} s{scope}"
    if ignored:
        took += f", {len(ignored)} tests that failed before your changes not counted"

    if passed:
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
                if ignored:
                    message += (
                        f"\nThese tests still fail, as they did before your changes: {', '.join(ignored[:8])}. "
                        "You have not fixed them: say so in the summary, do not call that code correct."
                    )
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
            elif not state.get("review_needed", True):  # a fast run ends with its last step
                state["review"] = "done"
                message = f"{head} All {total} steps are implemented.\n{ALL_DONE} Give the user a short summary."
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
            f"FAILED ({where}, attempt {state['attempts']}, {took}): {run_command}\n"
            f"{vf.summarize(result.output)}\n\n{advice}"
        )

    st.save(root, state)
    st.log_event(
        root,
        event="verify",
        step=(index + 1 if index is not None else "review"),
        passed=passed,
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
    passed, result, _ = run_tests(state, root, state["verify_command"], timeout)
    if not passed:
        return False, f"Verification fails: {state['verify_command']}\n{vf.summarize(result.output)}"
    return True, ALL_DONE

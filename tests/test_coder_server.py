import json
import os
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import PYTEST, write_test
from local_coder import state as st
from local_coder import verify as vf
from local_coder.server import ALL_DONE, REPEAT_HINT, REPEAT_STOP, check, make_plan, run_verify, show_status

FAILING = "def test_add():\n    assert 1 + 1 == 3\n"
PASSING = "def test_add():\n    assert 1 + 1 == 2\n"
MORE = PASSING + "\n\ndef test_sub():\n    assert 3 - 1 == 2\n"
CRITERIA = ["add(1, 1) == 2", "sub(3, 1) == 2"]


def finish_one_step_plan(project):
    """Plan one step, pass it and answer the review."""
    make_plan(project, "g", ["only"], CRITERIA, PYTEST)
    write_test(project, PASSING)
    assert "REVIEW before you finish" in run_verify(project)
    assert ALL_DONE in run_verify(project, note="test_add covers it")


def test_plan_needs_goal_steps_and_criteria(project):
    assert make_plan(project, "", ["a"], CRITERIA).startswith("Error")
    assert make_plan(project, "goal", [" "], CRITERIA).startswith("Error")
    assert make_plan(project, "goal", ["a"], []).startswith("Error")
    assert make_plan(project / "missing", "goal", ["a"], CRITERIA).startswith("Error")
    assert st.load(project) is None


def test_no_plan_and_no_command(project):
    assert "No plan" in run_verify(project) and "No plan" in show_status(project)
    out = make_plan(project, "Add things", ["first"], CRITERIA)
    assert "detected when verify runs" in out and "  2. sub(3, 1) == 2" in out
    assert run_verify(project).startswith("CANNOT VERIFY")
    assert st.load(project)["attempts"] == 0  # not counted as a failed attempt


def test_loop_until_done(project):
    out = make_plan(project, "Add things", ["write add()", "write sub()"], CRITERIA, PYTEST)
    assert "Now implement step 1: write add()" in out
    assert "Current step 1/2: write add()" in st.moim_file().read_text(encoding="utf-8")

    write_test(project, FAILING)
    out = run_verify(project)
    assert out.startswith("FAILED (step 1/2, attempt 1") and "assert" in out and "call verify again" in out
    assert "FAILED (attempt 1" in st.moim_file().read_text(encoding="utf-8")
    assert "[ ] 1. write add()   <- current" in show_status(project)

    write_test(project, PASSING)
    out = run_verify(project, note="added add()")
    assert out.startswith("PASSED (step 1/2") and "Next step 2/2: write sub()" in out
    state = st.load(project)
    assert state["steps"][0] == {"text": "write add()", "done": True, "note": "added add()"}
    assert state["attempts"] == 0
    assert check(project)[0] is False  # a step is still open

    # nothing was edited for step 2: passing tests do not close it
    out = run_verify(project)
    assert out.startswith("PASSED") and "step 2/2 stays open: write sub()" in out
    assert st.current(st.load(project)) == 1

    write_test(project, MORE)
    out = run_verify(project)
    assert out.startswith("PASSED (step 2/2") and "All 2 steps are implemented" in out
    assert "REVIEW before you finish" in out and "  1. add(1, 1) == 2" in out
    assert "in review" in st.moim_file().read_text(encoding="utf-8")
    ok, message = check(project)
    assert not ok and "the review is open" in message
    assert "REVIEW before you finish" in show_status(project)

    # the review needs an edit or a note
    assert "the review is still open" in run_verify(project)
    out = run_verify(project, note="test_add and test_sub cover both criteria")
    assert ALL_DONE in out
    assert st.load(project)["review_note"] == "test_add and test_sub cover both criteria"
    assert st.moim_file().read_text(encoding="utf-8") == st.IDLE
    assert check(project) == (True, ALL_DONE)
    assert "already finished" in run_verify(project) and ALL_DONE in show_status(project)

    events = [json.loads(line) for line in (project / ".local-coder" / "log.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["plan"] + ["verify"] * 7
    assert [e["step"] for e in events[1:]] == [1, 1, 2, 2, "review", "review", "review"]
    assert [e["passed"] for e in events[1:]] == [False, True, True, True, True, True, True]
    assert [e["unchanged"] for e in events[1:]] == [False, False, True, False, True, True, True]
    assert [e["done"] for e in events[1:]] == [False, False, False, False, False, True, True]
    assert (project / ".local-coder" / ".gitignore").read_text() == "*\n"


def test_review_can_be_answered_by_adding_tests(project):
    make_plan(project, "g", ["only"], CRITERIA, PYTEST)
    write_test(project, PASSING)
    assert "REVIEW before you finish" in run_verify(project)
    write_test(project, "def test_add():\n    assert 1 + 1 == 2\n\n\ndef test_sub():\n    assert 3 - 1 == 3\n")
    assert run_verify(project).startswith("FAILED (review, attempt 1")  # the new test uncovered a bug
    assert st.current(st.load(project)) is None  # the steps stay done
    write_test(project, MORE)
    assert ALL_DONE in run_verify(project)


def test_unchanged_step_needs_a_note(project):
    write_test(project, PASSING)
    make_plan(project, "g", ["first", "second"], CRITERIA, PYTEST)
    assert "step 1/2 stays open" in run_verify(project)  # the tests passed before any work
    assert "Next step 2/2" in run_verify(project, note="add() and test_add already cover it")
    assert "REVIEW before you finish" in run_verify(project, note="test_add covers it")


def test_snapshot_ignores_caches_and_state(project):
    write_test(project, PASSING)
    before = vf.snapshot(project)
    for folder in (".local-coder", "__pycache__", ".venv", "pkg.egg-info"):
        (project / folder).mkdir()
        (project / folder / "x").write_text("1", encoding="utf-8")
    assert vf.snapshot(project) == before
    write_test(project, FAILING)
    assert vf.snapshot(project) != before


def test_broken_after_done_reopens_the_review(project):
    finish_one_step_plan(project)
    write_test(project, FAILING)
    ok, message = check(project)
    assert not ok and message.startswith("Verification fails")
    assert run_verify(project).startswith("FAILED (review, attempt 1")
    state = st.load(project)
    assert st.current(state) is None and not st.finished(state)
    write_test(project, PASSING)
    assert ALL_DONE in run_verify(project)


def test_repeated_failure_changes_the_advice(project):
    make_plan(project, "g", ["only"], CRITERIA, PYTEST)
    write_test(project, FAILING)
    outs = [run_verify(project) for _ in range(REPEAT_STOP)]
    assert "Fix the cause" in outs[0] and "Fix the cause" in outs[REPEAT_HINT - 2]
    assert f"happened {REPEAT_HINT} times" in outs[REPEAT_HINT - 1] and "different hypothesis" in outs[REPEAT_HINT - 1]
    assert "Stop editing" in outs[-1]
    # a different failure starts the count again
    write_test(project, "def test_add():\n    raise TypeError('other')\n")
    out = run_verify(project)
    assert "Fix the cause" in out and f"attempt {REPEAT_STOP + 1}" in out


def test_check_command_line(project):
    env = {**os.environ, "LOCAL_CODER_HOME": os.environ["LOCAL_CODER_HOME"]}
    cmd = [sys.executable, "-m", "local_coder", "check"]
    make_plan(project, "g", ["only"], CRITERIA, PYTEST)
    write_test(project, PASSING)
    # run from another folder: the project is found through the active pointer
    assert subprocess.run(cmd, cwd=project.parent, env=env, capture_output=True).returncode == 1
    run_verify(project)
    assert subprocess.run(cmd, cwd=project.parent, env=env, capture_output=True).returncode == 1  # review open
    run_verify(project, note="test_add covers it")
    done = subprocess.run(cmd, cwd=project.parent, env=env, capture_output=True, text=True)
    assert done.returncode == 0 and "ALL DONE" in done.stdout
    assert subprocess.run([*cmd, str(project)], env=env, capture_output=True).returncode == 0


@pytest.mark.anyio
async def test_stdio_server(project):
    env = {**os.environ, "LOCAL_CODER_HOME": os.environ["LOCAL_CODER_HOME"]}
    params = StdioServerParameters(command=sys.executable, args=["-m", "local_coder"], env=env, cwd=project.parent)

    async def call(session, name, **arguments):
        result = await session.call_tool(name, arguments)
        return "\n".join(item.text for item in result.content if item.type == "text")

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert "call plan" in init.instructions
        assert {t.name for t in (await session.list_tools()).tools} == {"plan", "verify", "status"}

        assert "No plan" in await call(session, "status")
        out = await call(
            session, "plan", goal="g", steps=["only"], criteria=CRITERIA, verify_command=PYTEST, project=str(project)
        )
        assert f"Project: {project}" in out
        write_test(project, FAILING)
        assert (await call(session, "verify")).startswith("FAILED (step 1/1")
        write_test(project, PASSING)
        assert "REVIEW before you finish" in await call(session, "verify", note="fixed")
        assert ALL_DONE in await call(session, "verify", note="test_add covers it")
        assert ALL_DONE in await call(session, "status")


@pytest.mark.parametrize(
    "step",
    [
        "Write the tests",
        "Write comprehensive tests for all functions with given acceptance examples",
        "Run all tests and fix any failures",
        "Step 9: Test identity relations",
        "Write CLI tests with expected output",
        "Add unit tests",
    ],
)
def test_steps_that_are_only_about_tests_are_refused(project, step):
    out = make_plan(project, "g", ["Implement the parser", step], CRITERIA)
    assert out.startswith("Error: step 2") and "only about tests" in out
    assert st.load(project) is None


@pytest.mark.parametrize(
    "step",
    [
        "Implement the parser with its tests",
        "Add parse_date() and tests for invalid dates",
        "Write a failing test that reproduces the bug",
        "Write test_add.py",
        "Implement the CLI",
    ],
)
def test_other_steps_are_accepted(project, step):
    assert make_plan(project, "g", [step], CRITERIA).startswith("Plan saved")


def test_a_note_must_name_a_file_or_a_test_of_the_project(project):
    (project / "pkg").mkdir()
    (project / "pkg" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    write_test(project, PASSING)
    make_plan(project, "g", ["first", "second", "third", "fourth"], CRITERIA, PYTEST)
    assert "names no file or test" in run_verify(project, note="Step 1 passed. Advancing.")
    assert "names no file or test" in run_verify(project, note="engine2.py and test_nothing cover it")
    assert "Next step 2/4" in run_verify(project, note="pkg/engine.py implements it")
    assert "Next step 3/4" in run_verify(project, note="engine.py implements it")  # a base name is enough
    assert "Next step 4/4" in run_verify(project, note="tests/it: test_add covers it")  # a test of the project
    # edited files need no note at all
    write_test(project, MORE)
    assert "REVIEW before you finish" in run_verify(project)

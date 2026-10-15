"""The judge: the task's own examples run on the change, and a fresh conversation that can send a finished task
back."""

import subprocess

import pytest

from local_ai.coder import judge as jd
from local_ai.coder import state as st
from test_coder_agent import PASSING, call, make_agent, plan_call, say

ISSUE = """`add` is wrong for negative numbers.

```python
>>> from calc import add
>>> add(-1, -1)
0
>>> add(2, 2)
4
```

```
Traceback (most recent call last):
  File "<stdin>", line 1
ValueError: boom
```

Expected:

```python
from calc import add
print(add(-1, -1))
```
"""


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def repo(project):
    (project / "calc.py").write_text("def add(a, b):\n    return abs(a) + abs(b)\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "start")
    return project


def test_the_examples_of_a_task_are_its_python_blocks_without_prompts_and_output():
    assert jd.snippets(ISSUE) == [
        "from calc import add\nadd(-1, -1)\nadd(2, 2)",
        "from calc import add\nprint(add(-1, -1))",
    ]
    assert jd.snippets("no code here") == []


def test_examples_run_like_the_interactive_interpreter(repo):
    task = "```python\n1 + 1\nx = 5\nundefined_name\nprint(x * 2)\n```"
    output = jd.run_examples(task, repo)
    assert "2" in output and "NameError" in output and "10" in output  # the error does not stop the rest
    shown = jd.run_examples(ISSUE, repo)
    assert "Example 1" in shown and "Example 2" in shown and "\n2\n" in shown  # abs(-1) + abs(-1)


def test_the_diff_has_new_files_and_leaves_what_was_staged(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    git(repo, "add", "calc.py")
    (repo / "test_calc.py").write_text("def test_neg():\n    pass\n", encoding="utf-8")
    change = jd.diff(repo)
    assert "+    return a + b" in change and "New file test_calc.py" in change
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True)
    assert staged.stdout.split() == ["calc.py"]
    assert jd.diff(repo.parent) == ""  # not a repository


def test_the_verdict_is_read_from_the_answer():
    assert jd.parse("VERDICT: SOLVED") == (True, "")
    assert jd.parse("**VERDICT: NOT SOLVED**\nadd(-1, -1) is still 2.") == (False, "add(-1, -1) is still 2.")
    assert jd.parse("I think it is fine.") is None


@pytest.mark.anyio
async def test_the_judge_sends_a_finished_task_back_once_and_then_lets_it_end(repo):
    agent, client, events = make_agent(
        repo,
        [
            say(calls=[plan_call()]),
            say(calls=[call("write_file", path="test_it.py", content=PASSING), call("verify")]),
            say(calls=[call("verify", note="test_add covers it")]),
            say("VERDICT: NOT SOLVED\nadd(-1, -1) still returns 2."),  # the judge
            say(
                calls=[
                    call("write_file", path="calc.py", content="def add(a, b):\n    return a + b\n"),
                    call("verify", note="calc.py: add keeps the signs"),
                ]
            ),
            say("VERDICT: SOLVED"),  # the judge again
            say("Bitti."),
        ],
    )
    outcome = await agent.run(ISSUE)
    assert outcome.finished and outcome.summary == "Bitti."
    verdicts = [e for kind, e in events if kind == "judge"]
    assert [v["solved"] for v in verdicts] == [False, True]
    asked = client.requests[3]["messages"]
    assert [m["role"] for m in asked] == ["system", "user"] and "tools" not in client.requests[3]
    assert "add(-1, -1)" in asked[1]["content"] and "Example 1" in asked[1]["content"]
    assert "New file test_it.py" in asked[1]["content"]  # the diff
    back = client.requests[4]["messages"][-1]["content"]
    assert "NOT solved" in back and "add(-1, -1) still returns 2." in back
    assert st.finished(st.load(repo))


@pytest.mark.anyio
async def test_no_judge_without_rounds_or_in_a_fast_run(repo):
    for settings in ({"judge_rounds": 0}, {"speed": "fast"}):
        st.archive(repo)
        script = [
            say(calls=[plan_call()]),
            say(calls=[call("write_file", path="test_it.py", content=PASSING), call("verify")]),
            say(calls=[call("verify", note="test_add covers it")]),
            say("Bitti."),
        ]
        if settings.get("speed") == "fast":
            del script[2]  # a fast run has no review
        agent, client, events = make_agent(repo, script, **settings)
        outcome = await agent.run(ISSUE)
        assert outcome.finished and not [e for kind, e in events if kind == "judge"]

import argparse
import json
import sys

import pytest

from conftest import PYTEST
from local_ai.coder import collect as cl
from local_ai.coder import trace as tr
from test_coder_agent import FakeClient, call, say

STUB = "def add(a, b):\n    raise NotImplementedError\n"
TESTS = "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
GRADE = f'"{sys.executable}" -B -m pytest -q -p no:cacheprovider'


def args(tmp_path, tasks, **over):
    path = tmp_path / "tasks.jsonl"
    path.write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
    parser = argparse.ArgumentParser()
    cl.add_parser(parser.add_subparsers())
    a = parser.parse_args(["collect", str(path), "--out", str(tmp_path / "out"), "--model", "fake", "--samples", "1",
                           "--judge-rounds", "0", "--concurrency", "1"])
    return argparse.Namespace(**{**vars(a), **over})


def task(id_="add"):
    return {"id": id_, "prompt": "Write add(a, b) in solution.py.", "grade": GRADE,
            "files": {"solution.py": STUB, "test_solution.py": TESTS}, "protected": ["test_solution.py"]}


def solving(solution):
    plan = call("plan", goal="add", steps=["write add"], criteria=["add(2, 3) == 5"], verify_command=PYTEST)
    return [
        say(calls=[{"name": "load_skill", "arguments": json.dumps({"name": "new-feature"})}]),
        say(calls=[plan], thinking="Plan first."),
        say(calls=[call("write_file", path="solution.py", content=solution)]),
        say(calls=[call("verify")]),
        say(calls=[call("verify", note="test_add in test_solution.py covers it")]),  # the review
        say("Done."),
    ]


@pytest.mark.anyio
async def test_a_collection_grades_each_run_with_the_tests_and_goes_on_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))
    a = args(tmp_path, [task()])
    client = FakeClient(solving("def add(a, b):\n    return a + b\n"))
    assert await cl.collect(a, client) == 0
    trace = tmp_path / "out" / "traces" / "add-0.trace.jsonl"
    [run] = tr.runs(trace)
    assert run["finished"] and run["resolved"] is True
    [row] = [json.loads(line) for line in (tmp_path / "out" / "results.jsonl").read_text().splitlines()]
    assert row["resolved"] and row["turns"] == 6 and not (tmp_path / "out" / "work" / "add-0").exists()
    assert tr.export(tmp_path / "s.jsonl", folder=tmp_path / "out" / "traces") == (1, 1, 0)

    again = FakeClient([])  # nothing left to run: no request
    await cl.collect(a, again)
    assert not again.requests


@pytest.mark.anyio
async def test_changing_the_tests_does_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))
    a = args(tmp_path, [task()])
    script = solving("def add(a, b):\n    return 0\n")
    script.insert(3, say(calls=[call("write_file", path="test_solution.py", content="def test_add():\n    pass\n")]))
    await cl.collect(a, FakeClient(script))
    [run] = tr.runs(tmp_path / "out" / "traces" / "add-0.trace.jsonl")
    assert run["resolved"] is False  # the restored tests fail
    assert tr.export(tmp_path / "s.jsonl", folder=tmp_path / "out" / "traces") == (0, 0, 1)

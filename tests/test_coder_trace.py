import json

import pytest

from local_ai.coder import trace as tr
from test_coder_agent import FAILING, call, make_agent, plan_call, say


def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    return tmp_path / "ui" / "runs"


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.anyio
async def test_a_finished_run_is_traced_with_its_thinking_which_the_server_does_not_get_back(
    project, tmp_path, monkeypatch
):
    path = workspace(tmp_path, monkeypatch) / "chat.trace.jsonl"
    script = [
        say(calls=[plan_call()], thinking="First a plan."),
        say(calls=[call("write_file", path="test_it.py", content=FAILING)], thinking="Now the test."),
        say(calls=[call("edit_file", path="test_it.py", old="== 3", new="== 2"), call("verify", note="fixed")]),
        say(calls=[call("verify", note="test_add covers it")]),
        say("Bitti."),
    ]
    agent, client, _ = make_agent(project, script, judge_rounds=0)
    agent.trace = path
    assert (await agent.run("Add things")).finished
    assert not any("reasoning" in m for r in client.requests for m in r["messages"])  # thinking is not sent back
    [run] = lines(path)
    assert run["finished"] and run["reason"] == "ALL DONE" and run["judge"] is None and run["task"] == "Add things"
    assert {t["function"]["name"] for t in run["tools"]} >= {"plan", "verify", "write_file"}
    [segment] = run["segments"]
    replies = [m for m in segment if m["role"] == "assistant"]
    assert [m.get("reasoning") for m in replies[:3]] == ["First a plan.", "Now the test.", None]

    n, used, left = tr.export(tmp_path / "samples.jsonl")
    assert (n, used, left) == (1, 1, 0)
    [s] = lines(tmp_path / "samples.jsonl")
    assert s["messages"][0]["role"] == "system" and s["messages"][-1] == {"role": "assistant", "content": "Bitti."}
    assert s["messages"][2]["reasoning_content"] == "First a plan." and "reasoning" not in s["messages"][2]
    assert s["meta"]["reason"] == "ALL DONE" and s["meta"]["source"] == "chat.trace.jsonl" and s["tools"]


@pytest.mark.anyio
async def test_a_fresh_start_cuts_the_trace_into_segments(project, tmp_path, monkeypatch):
    path = workspace(tmp_path, monkeypatch) / "stuck.trace.jsonl"
    stuck = [say(finish="length", thinking=f"Maybe it is case {i}.") for i in range(4)]
    script = [say(calls=[plan_call()]), *stuck, say("Notes: f() is wrong."), say("x")]
    agent, _, _ = make_agent(project, script, max_turns=6)
    agent.trace = path
    assert not (await agent.run("Fix the sign bug")).finished
    [run] = lines(path)
    # after the fresh start: the instructions, the task, the restart note, a reply and the program's verify result
    assert len(run["segments"]) == 2
    assert [m["role"] for m in run["segments"][1]] == ["system", "user", "user", "assistant", "user"]
    assert tr.export(tmp_path / "good.jsonl") == (0, 0, 1)  # an unfinished run teaches nothing
    assert tr.export(tmp_path / "all.jsonl", failed=True) == (2, 1, 0)


def test_export_takes_only_good_runs_and_the_longest_of_a_chat_that_went_on(tmp_path, monkeypatch):
    runs = workspace(tmp_path, monkeypatch)
    runs.mkdir(parents=True)
    system, task = {"role": "system", "content": "s"}, {"role": "user", "content": "t"}
    first = [system, task, {"role": "assistant", "content": "a"}]
    later = [*first, {"role": "user", "content": "more"}, {"role": "assistant", "content": "b"}]

    def run(segment, **fields):
        return json.dumps({"finished": True, "reason": "ALL DONE", "tools": [], "segments": [segment], **fields})

    (runs / "a.trace.jsonl").write_text("\n".join([run(first), run(later)]) + "\n", encoding="utf-8")
    (runs / "b.trace.jsonl").write_text(run(first, judge=False) + "\n", encoding="utf-8")  # the judge said no
    bench = tmp_path / "bench" / "results" / "x"
    bench.mkdir(parents=True)
    (bench / "t.trace.jsonl").write_text(run(later, task="bench") + "\n" + json.dumps({"resolved": False}) + "\n")
    n, used, left = tr.export(tmp_path / "out.jsonl")
    assert (n, used, left) == (1, 2, 2)
    [s] = lines(tmp_path / "out.jsonl")
    assert [m["content"] for m in s["messages"]] == ["s", "t", "a", "more", "b"]

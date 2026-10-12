import json
from types import SimpleNamespace

import pytest

from conftest import PYTEST
from local_ai.coder import prompts
from local_ai.coder import state as st
from local_ai.coder.loop import CoderAgent, Settings
from local_ai.core.llm import LLM
from local_ai.core.text import parse_inline_tool_calls, size

CRITERIA = ["add(1, 1) == 2"]
PASSING = "def test_add():\n    assert 1 + 1 == 2\n"
FAILING = "def test_add():\n    assert 1 + 1 == 3\n"


def call(name, **args):
    return {"name": name, "arguments": json.dumps(args)}


def say(text="", calls=(), finish="stop", thinking=""):
    return {"text": text, "calls": list(calls), "finish": finish, "thinking": thinking}


class FakeClient:
    """Plays scripted replies through the same streaming interface as openai.AsyncOpenAI."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # the loop keeps appending
        reply = self.script.pop(0)

        async def stream():
            if reply["thinking"]:
                yield self.chunk(reasoning_content=reply["thinking"])
            if reply["text"]:
                yield self.chunk(content=reply["text"])
            for i, c in enumerate(reply["calls"]):
                fn = SimpleNamespace(name=c["name"], arguments=c["arguments"])
                yield self.chunk(tool_calls=[SimpleNamespace(index=i, id=f"id{i}", function=fn)])
            yield self.chunk(finish=reply["finish"])
            yield SimpleNamespace(choices=[], usage=SimpleNamespace(completion_tokens=42))

        return stream()

    @staticmethod
    def chunk(finish=None, **delta):
        fields = {"content": None, "tool_calls": None, "reasoning_content": None, **delta}
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields), finish_reason=finish)])


def make_agent(project, script, **settings):
    client = FakeClient(script)
    settings.setdefault("require_skill", False)  # tests of the skill gate set it
    events = []
    agent = CoderAgent(
        LLM(client, "fake"), project, Settings(**settings), emit=lambda kind, **data: events.append((kind, data))
    )
    return agent, client, events


def plan_call():
    return call("plan", goal="g", steps=["only"], criteria=CRITERIA, verify_command=PYTEST)


@pytest.mark.anyio
async def test_task_ends_only_when_verify_says_all_done(project):
    agent, client, events = make_agent(
        project,
        [
            say(calls=[plan_call()], thinking="think"),
            say(calls=[call("write_file", path="test_it.py", content=FAILING)]),
            say(calls=[call("verify")]),
            say(calls=[call("edit_file", path="test_it.py", old="== 3", new="== 2"), call("verify", note="fixed")]),
            say(calls=[call("verify", note="test_add covers it")]),
            say("Bitti, ozet."),
        ],
    )
    outcome = await agent.run("Add things")
    assert outcome.finished and outcome.summary == "Bitti, ozet."
    assert st.finished(st.load(project))
    first = client.requests[0]
    assert first["max_tokens"] == 8192 and first["extra_body"] == {"top_k": 20} and first["stream"] is True
    assert {t["function"]["name"] for t in first["tools"]} >= {"plan", "verify", "write_file"}
    assert "tools" not in client.requests[-1]  # the summary turn has no tools
    assert [e["tokens"] for k, e in events if k == "turn"][0] == 42
    turns = [e for k, e in events if k == "turn"]
    assert turns[0]["max_turns"] == 80 and turns[0]["context_limit"] == 300_000  # for the UI's meter
    assert 0 < turns[0]["context"] < turns[-1]["context"]


@pytest.mark.anyio
async def test_files_cannot_be_written_before_the_plan(project):
    agent, _, _ = make_agent(project, [])
    out = await agent.call_tool(
        {"id": "x", "function": {"name": "write_file", "arguments": '{"path":"a","content":"b"}'}}
    )
    assert "no plan yet" in out and not (project / "a").exists()


@pytest.mark.anyio
async def test_bad_tool_calls_come_back_as_text(project):
    agent, _, _ = make_agent(project, [])

    async def run(name, arguments):
        return await agent.call_tool({"id": "x", "function": {"name": name, "arguments": arguments}})

    assert "unknown tool" in await run("shell", "{}")
    assert "not valid JSON" in await run("verify", '{"note": ')
    assert "must be a JSON object" in await run("verify", "[1]")
    assert "wrong arguments" in await run("read_file", '{"nope": 1}')
    assert "No plan" in await run("verify", "")


@pytest.mark.anyio
async def test_cut_off_reply_gets_a_nudge_not_a_wait(project):
    agent, client, _ = make_agent(
        project,
        [say(thinking="x" * 50, finish="length"), say(calls=[plan_call()]), say("gave up"), say("again")],
        max_nudges=1,
    )
    outcome = await agent.run("task")
    assert not outcome.finished and "stopped before" in outcome.reason
    sent = client.requests[1]["messages"]
    assert sent[-1] == {"role": "user", "content": prompts.CUT_OFF}


@pytest.mark.anyio
async def test_gives_up_after_repeated_cut_offs(project):
    script = [say(finish="length") for _ in range(4)]
    outcome = await make_agent(project, script, max_nudges=3)[0].run("task")
    assert not outcome.finished and "out of tokens" in outcome.reason


@pytest.mark.anyio
async def test_stopping_early_makes_the_program_run_verify(project):
    agent, client, events = make_agent(
        project,
        [
            say("Plan: I will do it."),  # no plan yet
            say(calls=[plan_call()]),
            say(calls=[call("write_file", path="test_it.py", content=FAILING)]),
            say("Done!"),  # claims done; the program runs verify
            say(calls=[call("edit_file", path="test_it.py", old="== 3", new="== 2")]),
            say("Done again"),  # verify passes -> review
            say("Still done"),  # review open: the program runs verify, no file changed
            say(calls=[call("verify", note="test_add covers it")]),
            say("ozet"),
        ],
        max_nudges=10,
    )
    outcome = await agent.run("task")
    assert outcome.finished
    sent = [m["content"] for r in client.requests for m in r["messages"] if m["role"] == "user"]
    assert prompts.NEEDS_PLAN in sent
    assert any(s.startswith("You stopped, but the task is not finished") and "FAILED" in s for s in sent)
    assert [k for k, _ in events].count("auto_verify") >= 2


@pytest.mark.anyio
async def test_edits_without_verify_trigger_verify(project):
    edits = [call("write_file", path=f"f{i}.txt", content="x") for i in range(3)]
    agent, client, events = make_agent(
        project,
        [
            say(calls=[plan_call()]),
            say(calls=[call("write_file", path="test_it.py", content=FAILING), *edits]),
            say(calls=[call("verify")] * 0 or [call("status")]),
            say("stop"),
        ],
        edits_before_verify=4,
        max_nudges=0,
    )
    await agent.run("task")
    auto = [d["output"] for k, d in events if k == "auto_verify"]
    assert auto and auto[0].startswith("FAILED")
    users = [m["content"] for m in client.requests[2]["messages"] if m["role"] == "user"]
    assert any(u.startswith("You made 4 edits without calling verify") for u in users)


@pytest.mark.anyio
async def test_stops_when_the_same_failure_repeats(project):
    script = [say(calls=[plan_call()]), say(calls=[call("write_file", path="test_it.py", content=FAILING)])]
    script += [say(calls=[call("verify")]) for _ in range(8)]
    outcome = await make_agent(project, script)[0].run("task")
    assert not outcome.finished and "same failure happened 6 times" in outcome.reason


@pytest.mark.anyio
async def test_turn_limit(project):
    script = [say(calls=[call("status")]) for _ in range(3)]
    outcome = await make_agent(project, script, max_turns=3)[0].run("task")
    assert not outcome.finished and "turn limit" in outcome.reason


@pytest.mark.anyio
async def test_compaction_keeps_the_plan_and_pairs(project):
    agent, client, events = make_agent(
        project,
        [say(calls=[plan_call()])]
        + [say(calls=[call("read_file", path="test_it.py")]) for _ in range(3)]
        + [say("end")],
        context_chars=2500,
        keep_messages=4,
        notes_tokens=0,
        max_nudges=0,
    )
    (project / "test_it.py").write_text(PASSING, encoding="utf-8")
    await agent.run("task")
    assert any(k == "compact" for k, _ in events)
    messages = client.requests[-1]["messages"]
    assert messages[2]["content"].startswith("Earlier messages were dropped") and "Goal: g" in messages[2]["content"]
    assert "test_it.py" in messages[2]["content"]
    roles = [m["role"] for m in messages]
    assert roles[0] == "system" and roles[1] == "user"
    # no tool result without the assistant call before it
    for i, m in enumerate(messages):
        if m["role"] == "tool":
            assert messages[i - 1]["role"] in ("assistant", "tool")
    assert size(messages) < 2500 + len(messages[0]["content"])  # the system prompt is not counted in the limit


@pytest.mark.anyio
async def test_resumes_an_unfinished_plan(project):
    agent, client, _ = make_agent(project, [say(calls=[plan_call()]), say("stop")], max_nudges=0)
    await agent.run("task")  # leaves a plan with open steps
    again, client2, _ = make_agent(project, [say("ok")], max_nudges=0)
    await again.run("task")
    first = client2.requests[0]["messages"][1]["content"]
    assert "continue it instead of starting over" in first and "[ ] 1. only" in first


def test_inline_tool_calls_are_found():
    calls = parse_inline_tool_calls('<tool_call>{"name": "verify", "arguments": {"note": "x"}}</tool_call>')
    assert calls[0]["function"]["name"] == "verify" and json.loads(calls[0]["function"]["arguments"]) == {"note": "x"}


@pytest.mark.anyio
async def test_real_openai_client_against_a_streaming_server(project):
    """The OpenAI client parses a real SSE stream (content, reasoning, split tool call, usage) into one Reply."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from openai import AsyncOpenAI

    seen = []
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "hmm"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "Working. "}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                      "function": {"name": "read_", "arguments": '{"pa'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
                      "function": {"name": "file", "arguments": 'th": "a.py"}'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}},
    ]  # fmt: skip

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(
                    f"data: {json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'm', **chunk})}\n\n".encode()
                )
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = AsyncOpenAI(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="x")
        reply = await LLM(client, "m", presence_penalty=0.5).complete([{"role": "user", "content": "hi"}], [])
    finally:
        server.shutdown()
    assert reply.content == "Working. " and reply.reasoning_chars == 3
    assert reply.tool_calls[0]["function"] == {"name": "read_file", "arguments": '{"path": "a.py"}'}
    assert reply.finish_reason == "tool_calls" and reply.completion_tokens == 9
    assert seen[0]["top_k"] == 20 and seen[0]["presence_penalty"] == 0.5 and seen[0]["max_tokens"] == 8192


@pytest.mark.anyio
async def test_ui_events_tokens_and_state(project):
    agent, _, events = make_agent(
        project, [say("working", calls=[plan_call()], thinking="think"), say("stop")], max_nudges=0
    )
    await agent.run("task")
    kinds = [k for k, _ in events]
    tokens = [d for k, d in events if k == "token"]
    assert {"text": "think", "thinking": True} in tokens and {"text": "working", "thinking": False} in tokens
    assert kinds.count("stream_end") == 2
    state = [d for k, d in events if k == "state"][-1]
    assert state["goal"] == "g" and state["current"] == 0 and state["steps"] == [{"text": "only", "done": False}]
    assert state["criteria"] == CRITERIA and state["finished"] is False
    call_event = next(d for k, d in events if k == "tool_call")
    assert call_event["id"] == "id0" and json.loads(call_event["arguments"])["goal"] == "g"
    assert next(d for k, d in events if k == "tool_result")["error"] is False


def compaction_agent(project, script):
    """An agent that asks for a compaction after its third tool result."""
    agent, client, events = make_agent(project, script, keep_messages=2, max_nudges=0)
    emit = agent.emit

    def trigger(kind, **data):
        emit(kind, **data)
        if kind == "tool_result" and [k for k, _ in events].count("tool_result") == 3:
            agent.request_compact()

    agent.emit = trigger
    return agent, client, events


@pytest.mark.anyio
async def test_manual_compaction_keeps_the_models_own_notes(project):
    (project / "a.txt").write_text("x", encoding="utf-8")
    read = call("read_file", path="a.txt")
    agent, client, events = compaction_agent(
        project,
        [say(calls=[plan_call()]), say(calls=[read]), say(calls=[read]), say("NOTE: a.txt holds x."), say("end")],
    )
    await agent.run("task")
    compact = next(d for k, d in events if k == "compact")
    assert compact == {"dropped": 4, "noted": True, "manual": True}
    asked = client.requests[3]["messages"]  # the notes request: its own system prompt, no tools
    assert asked[0]["content"] == prompts.COMPACT_SYSTEM and "tools" not in client.requests[3]
    assert "read_file" in asked[1]["content"]
    after = client.requests[4]["messages"]
    assert "Your notes on the dropped part" in after[2]["content"] and "NOTE: a.txt holds x." in after[2]["content"]
    assert "Goal: g" in after[2]["content"]


@pytest.mark.anyio
async def test_compaction_without_notes_when_the_model_cannot_write_them(project):
    read = call("read_file", path="a.txt")
    agent, client, events = compaction_agent(
        project, [say(calls=[plan_call()]), say(calls=[read]), say(calls=[read]), say(finish="length"), say("end")]
    )
    await agent.run("task")
    assert next(d for k, d in events if k == "compact")["noted"] is False
    assert "Your notes" not in client.requests[4]["messages"][2]["content"]
    assert "Goal: g" in client.requests[4]["messages"][2]["content"]


@pytest.mark.anyio
async def test_compaction_with_nothing_to_drop_is_a_no_op(project):
    agent, client, events = make_agent(project, [say("end")], max_nudges=0)
    agent.request_compact()
    await agent.run("task")
    assert next(d for k, d in events if k == "compact")["dropped"] == 0 and len(client.requests) == 1


@pytest.mark.anyio
async def test_a_cut_off_tool_call_is_not_run_or_kept(project):
    cut = {"name": "write_file", "arguments": '{"path": "big.py", "content": "def f():'}  # cut off: broken JSON
    agent, client, _ = make_agent(
        project, [say(calls=[plan_call()]), say(calls=[cut], finish="length"), say("stop")], max_nudges=1
    )
    await agent.run("task")
    assert not (project / "big.py").exists()
    sent = client.requests[2]["messages"]
    assert sent[-1] == {"role": "user", "content": prompts.CUT_OFF_CALL}
    assert not any("big.py" in json.dumps(m) for m in sent)  # the broken call is nowhere in the history


@pytest.mark.anyio
async def test_invalid_json_arguments_never_stay_in_the_history(project):
    bad = {"name": "verify", "arguments": '{"note": '}
    agent, client, events = make_agent(project, [say(calls=[bad]), say("stop")], max_nudges=0)
    await agent.run("task")
    sent = client.requests[1]["messages"]
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "not valid JSON" in sent[3]["content"]
    assert next(d for k, d in events if k == "tool_result")["error"] is True


@pytest.mark.anyio
async def test_a_failing_model_server_ends_the_run_readably(project):
    class Down:
        chat = SimpleNamespace(completions=SimpleNamespace(create=None))

    async def create(**kwargs):
        raise RuntimeError("<!DOCTYPE html><html><body><pre>Internal Server Error</pre></body></html>")

    Down.chat.completions.create = create
    outcome = await CoderAgent(LLM(Down(), "m"), project).run("task")
    assert not outcome.finished
    assert outcome.reason == "the model server failed: RuntimeError: Internal Server Error"


@pytest.mark.anyio
async def test_thinking_without_an_answer_is_stopped_early(project):
    closed = []

    class Stream:
        def __init__(self):
            self.sent = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.sent += 1
            if self.sent > 1000:
                raise AssertionError("the stream was not stopped")
            return FakeClient.chunk(reasoning_content="x" * 100)

        async def close(self):
            closed.append(self.sent)

    stream = Stream()

    async def create(**kwargs):
        return stream

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reply = await LLM(client, "m", max_thinking_chars=1000).complete([{"role": "user", "content": "hi"}])
    assert reply.finish_reason == "length" and not reply.content and not reply.tool_calls
    assert reply.reasoning_chars == 1100 and closed == [11]  # stopped after 11 chunks, and closed


@pytest.mark.anyio
async def test_the_first_plan_is_refused_until_a_skill_is_read(project):
    plan = call(
        "plan", goal="Numerical integral of the orbit", steps=["step"], criteria=CRITERIA, verify_command=PYTEST
    )
    agent, client, _ = make_agent(
        project, [say(calls=[plan]), say(calls=[plan]), say("stop")], require_skill=True, max_nudges=0
    )
    await agent.run("task")
    refusal = client.requests[1]["messages"][-1]["content"]
    assert refusal.startswith("Error: the plan was not saved") and "(scientific-code)" in refusal
    assert "closed-form limits" in refusal  # the body of the skill is attached
    assert st.load(project) is not None  # the second plan was accepted


@pytest.mark.anyio
async def test_a_skill_read_before_the_plan_lets_it_through(project):
    agent, client, _ = make_agent(
        project,
        [
            say(calls=[{"name": "load_skill", "arguments": json.dumps({"name": "bug-fix"})}]),
            say(calls=[plan_call()]),
            say("stop"),
        ],
        require_skill=True,
        max_nudges=0,
    )
    await agent.run("task")
    assert st.load(project) is not None
    assert not any("the plan was not saved" in json.dumps(r["messages"]) for r in client.requests)


@pytest.mark.anyio
async def test_a_running_plan_is_not_gated(project):
    agent, _, _ = make_agent(project, [say(calls=[plan_call()]), say("stop")], max_nudges=0)  # first run: no gate
    await agent.run("task")
    again, client, _ = make_agent(project, [say(calls=[plan_call()]), say("stop")], require_skill=True, max_nudges=0)
    await again.run("task")
    assert "the plan was not saved" not in json.dumps(client.requests[1]["messages"])


@pytest.mark.anyio
async def test_a_message_sent_while_it_works_reaches_the_model_before_its_next_reply(project):
    agent, client, _ = make_agent(
        project,
        [
            say(calls=[plan_call()]),
            say(calls=[call("write_file", path="test_it.py", content=PASSING)]),
            say(calls=[call("verify", note="done")]),
            say(calls=[call("verify", note="test_add covers it")]),
            say("Bitti."),
        ],
    )
    agent.steer("Name the test file test_it.py")
    outcome = await agent.run("Add things")
    assert outcome.finished and not agent.steering
    first = client.requests[0]["messages"]
    assert first[-1]["role"] == "user" and "Name the test file test_it.py" in first[-1]["content"]
    assert "while you were working" in first[-1]["content"]


@pytest.mark.anyio
async def test_pages_of_a_paper_are_kept_longer_than_other_tool_output(project):
    from local_ai.core.tools import Tool

    page = "equation " * 1000  # 9000 characters
    tools = {
        name: Tool(name, "", {"type": "object", "properties": {}}, lambda: page)
        for name in ("library__read_pdf", "web__search")
    }
    script = [say(calls=[call("library__read_pdf"), call("web__search")]), say("stop"), say("stop")]
    client = FakeClient(script)
    agent = CoderAgent(LLM(client, "fake"), project, Settings(require_skill=False, max_nudges=1), extra_tools=tools)
    await agent.run("Implement the paper")
    read, searched = (m["content"] for m in client.requests[1]["messages"] if m["role"] == "tool")
    assert read == page and "characters omitted" in searched and len(searched) < 6500


@pytest.mark.anyio
async def test_the_fitting_skill_is_given_with_the_task(project):
    task = "Implement the algorithm of the paper arxiv.org/abs/1412.6980"
    agent, client, events = make_agent(project, [say(calls=[plan_call()]), say("stop")], require_skill=True, max_nudges=0)
    await agent.run(task)
    first = client.requests[0]["messages"][1]["content"]
    assert first.startswith(task) and "The skill for this kind of task is from-paper" in first
    assert "library__read_pdf" in first  # its body
    assert st.load(project) is not None  # the first plan is not refused
    assert ("log", {"text": "Skill given with the task: from-paper"}) in events

    # a task without a keyword gets no skill, and a running plan is continued without one
    assert "The skill for this kind of task" not in prompts.first_message("something unrelated", project, None)
    again, client, _ = make_agent(project, [say("stop"), say("stop")], require_skill=True, max_nudges=0)
    await again.run(task)
    assert "The skill for this kind of task" not in client.requests[0]["messages"][1]["content"]


@pytest.mark.anyio
async def test_a_finished_plan_does_not_end_the_next_task(project):
    done = [
        say(calls=[plan_call()]),
        say(calls=[call("write_file", path="test_it.py", content=PASSING)]),
        say(calls=[call("verify", note="done")]),
        say(calls=[call("verify", note="test_add covers it")]),
        say("Bitti."),
    ]
    first, _, _ = make_agent(project, done)
    assert (await first.run("Add things")).finished

    # the next task: reading a file or stopping must not count as finished, the old plan is not this task's
    agent, client, _ = make_agent(
        project,
        [
            say(calls=[call("read_file", path="test_it.py")]),
            say("It looks fine."),
            say(calls=[plan_call()]),
            say(calls=[call("verify", note="test_add in test_it.py covers it")]),
            say(calls=[call("verify", note="test_add in test_it.py covers add(1, 1) == 2")]),
            say("Simdi bitti."),
        ],
    )
    outcome = await agent.run("Change it")
    assert outcome.finished and outcome.summary == "Simdi bitti."
    assert "belongs to an earlier task" in client.requests[0]["messages"][1]["content"]
    assert "tools" in client.requests[1]  # after read_file the model still has its tools: no early wrap-up
    assert client.requests[2]["messages"][-1]["content"] == prompts.NEEDS_NEW_PLAN


def test_tool_calls_written_as_function_markup_are_parsed():
    from local_ai.core.text import strip_tool_calls

    content = (
        "I will look.\n<tool_call>\n<function=edit_file>\n<parameter=path>\ncosmo.py\n</parameter>\n"
        "<parameter=old>\na\nb\n</parameter>\n<parameter=steps>[\"x\", \"y\"]</parameter>\n"
        "<parameter=start>3</parameter>\n</function>\n</tool_call>"
    )
    (found,) = parse_inline_tool_calls(content)
    assert found["function"]["name"] == "edit_file"
    assert json.loads(found["function"]["arguments"]) == {"path": "cosmo.py", "old": "a\nb", "steps": ["x", "y"], "start": 3}
    assert strip_tool_calls(content) == "I will look."


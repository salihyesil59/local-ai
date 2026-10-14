"""Chat mode: a plain conversation with the model, with images and the MCP tools the user switched on."""

import asyncio
import json
import time

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.chat import CUT_OFF, ChatAgent  # noqa: E402
from local_ai.core.llm import LLM  # noqa: E402
from local_ai.core.tools import Tool  # noqa: E402
from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_images import PNG, URL  # noqa: E402
from test_web import read_events  # noqa: E402
from test_web_code import ScriptedClient, own_workspace  # noqa: E402,F401


@pytest.mark.anyio
async def test_a_chat_answers_and_keeps_the_conversation(tmp_path):
    client = ScriptedClient([("Merhaba!", []), ("Az önce selam verdin.", [])])
    events = []
    agent = ChatAgent(LLM(client, "m"), emit=lambda kind, **data: events.append(kind))
    assert await agent.reply("Selam") == "Merhaba!"
    picture = tmp_path / "a.png"
    picture.write_bytes(PNG)
    assert await agent.reply("Ne dedim?", [picture]) == "Az önce selam verdin."
    sent = client.requests[1]
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]
    assert sent["messages"][3]["content"][1]["image_url"]["url"] == URL
    assert "tools" not in sent  # no tools switched on: a plain chat
    assert events.count("turn") == 2 and "tool_call" not in events


@pytest.mark.anyio
async def test_a_chat_may_call_the_tools_that_are_switched_on():
    async def search(query: str) -> str:
        return f"3 papers about {query}"

    tool = Tool("library__search", "Search papers", {"type": "object", "properties": {"query": {"type": "string"}}}, search)
    calls = [{"name": "library__search", "arguments": json.dumps({"query": "dark energy"})}]
    client = ScriptedClient([("", calls), ("Üç makale buldum.", [])])
    agent = ChatAgent(LLM(client, "m"), tools={tool.name: tool})
    assert await agent.reply("Karanlık enerji makaleleri?") == "Üç makale buldum."
    assert client.requests[0]["tools"] == client.requests[1]["tools"]
    assert agent.messages[-2] == {"role": "tool", "tool_call_id": "id0", "content": "3 papers about dark energy"}


@pytest.mark.anyio
async def test_a_long_chat_drops_its_oldest_messages_and_a_cut_off_reply_says_so():
    client = ScriptedClient([("x" * 50, []), ("y", [])])
    agent = ChatAgent(LLM(client, "m"), context_chars=150)
    await agent.reply("a" * 60)
    agent.messages.append({"role": "user", "content": "b" * 60})  # a stopped message, then a new one
    await agent.reply("c" * 60)
    roles = [m["role"] for m in agent.messages]
    assert roles[0] == "system" and roles[1] == "user" and "a" * 60 not in str(agent.messages)

    class Cut(ScriptedClient):
        @staticmethod
        def chunk(finish=None, **delta):
            return ScriptedClient.chunk("length" if finish else None, **delta)

    assert (await ChatAgent(LLM(Cut([("Yarım", [])]), "m")).reply("?")) == "Yarım" + CUT_OFF


def test_a_chat_in_the_ui_goes_on_with_its_conversation():
    client = ScriptedClient([("Merhaba!", []), ("Selam verdin.", [])])
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        assert ui.post("/api/runs", json={"mode": "chat", "question": ""}).status_code == 400
        run = ui.post("/api/runs", json={"mode": "chat", "question": "Selam", "servers": []}).json()
        events = read_events(ui, run["id"])
        assert run["mode"] == "chat" and [e["text"] for e in events if e["kind"] == "answer"] == ["Merhaba!"]
        assert ui.post(f"/api/runs/{run['id']}/messages", json={"question": ""}).status_code == 400
        assert ui.post(f"/api/runs/{run['id']}/messages", json={"question": "Ne dedim?"}).status_code == 202
        events = read_events(ui, run["id"])
        assert [e["text"] for e in events if e["kind"] == "answer"] == ["Merhaba!", "Selam verdin."]
        assert [m["role"] for m in client.requests[1]["messages"]] == ["system", "user", "assistant", "user"]


@pytest.mark.anyio
async def test_a_reply_that_only_thought_is_asked_once_to_answer_now():
    class ThinksFirst(ScriptedClient):
        async def create(self, **kwargs):
            stream = await super().create(**kwargs)
            first = len(self.requests) == 1

            async def cut():
                async for chunk in stream:
                    if first and chunk.choices[0].finish_reason:
                        chunk = ScriptedClient.chunk("length")
                    yield chunk

            return cut()

    client = ThinksFirst([("", []), ("67.4 km/s/Mpc.", [])])
    agent = ChatAgent(LLM(client, "m"))
    assert await agent.reply("Hubble sabiti?") == "67.4 km/s/Mpc."
    assert client.requests[1]["messages"][-1]["content"].startswith("Your last reply was cut off")
    assert client.requests[1]["temperature"] == 0.9  # sampled more freely
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant"]  # the request is not kept


def test_a_message_sent_while_a_chat_answers_is_queued_and_can_be_sent_now():
    class Slow(ScriptedClient):
        async def create(self, **kwargs):
            if not self.requests:  # the first answer takes long: the user sends the next message meanwhile
                self.requests.append(kwargs)
                await asyncio.sleep(60)
            return await super().create(**kwargs)

    client = Slow([("İkinciye cevap.", [])])
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        run = ui.post("/api/runs", json={"mode": "chat", "question": "Birinci"}).json()
        chat = ui.app.state.runs[run["id"]]
        while not client.requests:  # it has started answering
            time.sleep(0.01)
        assert ui.post(f"/api/runs/{run['id']}/messages", json={"question": "İkinci"}).status_code == 202
        assert chat.agent.steering == [("İkinci", [])] and chat.events[-1] == {"kind": "steer", "text": "İkinci"}

        assert ui.post(f"/api/runs/{run['id']}/send-now", json={"servers": []}).status_code == 202
        events = read_events(ui, run["id"])
        assert {"kind": "user", "text": "İkinci", "queued": True} in events  # on the page already, as a steer
        assert [e["text"] for e in events if e["kind"] == "answer"] == ["İkinciye cevap."]
        assert [m["content"] for m in client.requests[-1]["messages"][1:]] == ["Birinci", "İkinci"]  # kept
        assert ui.post(f"/api/runs/{run['id']}/send-now", json={}).status_code == 409  # nothing queued


@pytest.mark.anyio
async def test_the_runner_answers_the_queued_messages_in_turn():
    from local_ai.web.runs import Run, run_chat

    run = Run(id="r", question="Birinci", project="", mode="chat")

    class Meanwhile(ScriptedClient):
        async def create(self, **kwargs):
            if not self.requests:  # the second message arrives while the first answer is written
                run.agent.steer("İkinci")
            return await super().create(**kwargs)

    client = Meanwhile([("Bir.", []), ("İki.", [])])
    await run_chat(run, "Birinci", {}, AgentSettings(model="m"), client, None)
    assert [e["text"] for e in run.events if e["kind"] == "answer"] == ["Bir.", "İki."]
    assert {"kind": "dequeued"} in run.events
    assert [m["role"] for m in client.requests[1]["messages"]] == ["system", "user", "assistant", "user"]


def test_a_question_sent_during_a_research_waits_for_it():
    from local_ai.web.runs import Run

    with TestClient(create_app([], AgentSettings(model="test"), client=ScriptedClient([]))) as ui:
        ui.app.state.runs["x"] = run = Run(id="x", question="Derin soru", project="p", status="running")
        assert ui.post("/api/runs/x/messages", json={"question": "Basit soru"}).status_code == 202
        assert run.queued == [("Basit soru", [])] and run.events[-1] == {"kind": "steer", "text": "Basit soru"}
        run.events.append({"kind": "report", "text": "Derin cevap."})
        task = run.follow_up("Basit soru", "Derin soru")
        assert task.startswith("Basit soru") and "Derin soru" in task and "Derin cevap." in task


def test_chats_of_every_mode_work_at_the_same_time_but_not_in_one_folder(tmp_path):
    class Busy(ScriptedClient):
        async def create(self, **kwargs):
            if kwargs["messages"][0]["content"].startswith("You are a coding agent"):
                self.requests.append(kwargs)
                await asyncio.sleep(60)  # a code run that works for long
            return await super().create(**kwargs)

    client = Busy([("Hemen.", [])])
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        def code(folder):
            body = {"mode": "code", "question": "x", "folder": str(tmp_path / folder)}
            return ui.app.state.runs[ui.post("/api/runs", json=body).json()["id"]]

        first, other = code("app"), code("lib")
        while len(client.requests) < 2:  # both work: different folders
            time.sleep(0.01)
        same = code("app")
        run = ui.post("/api/runs", json={"mode": "chat", "question": "Basit soru"}).json()
        events = read_events(ui, run["id"])
        assert [e["text"] for e in events if e["kind"] == "answer"] == ["Hemen."]  # beside two code runs
        assert (first.status, other.status, same.status) == ("running", "running", "queued")  # one folder: waits
        for r in (first, other, same):
            ui.post(f"/api/runs/{r.id}/cancel")

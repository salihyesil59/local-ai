"""Chat mode: a plain conversation with the model, with images and the MCP tools the user switched on."""

import json

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

"""Helpers of the web UI tests: a workspace of its own per test and a scripted model."""

import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def own_workspace(tmp_path, monkeypatch):
    """The UI saves its chats in the workspace: give each test its own."""
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path / "ws"))


def call(name, **args):
    return {"name": name, "arguments": json.dumps(args)}


class ScriptedClient:
    """Plays replies through the streaming interface of openai.AsyncOpenAI."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # the conversation keeps growing
        text, calls = self.replies.pop(0)

        async def stream():
            if text:
                yield self.chunk(content=text)
            for i, c in enumerate(calls):
                fn = SimpleNamespace(name=c["name"], arguments=c["arguments"])
                yield self.chunk(tool_calls=[SimpleNamespace(index=i, id=f"id{i}", function=fn)])
            yield self.chunk(finish="stop")

        return stream()

    @staticmethod
    def chunk(finish=None, **delta):
        fields = {"content": None, "tool_calls": None, "reasoning_content": None, **delta}
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(**fields), finish_reason=finish)], usage=None
        )

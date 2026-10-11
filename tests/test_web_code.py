"""Code mode of the web UI: the local-coder agent runs a scripted model in a temporary project folder."""

import json
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")
pytest.importorskip("local_coder")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.agent.config import AgentSettings  # noqa: E402
from local_ai.agent.web.app import create_app  # noqa: E402
from test_web import read_events  # noqa: E402


@pytest.fixture(autouse=True)
def own_workspace(tmp_path, monkeypatch):
    """The UI saves its chats in the workspace: give each test its own."""
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))


PYTEST = f'"{sys.executable}" -B -m pytest -x -q --tb=short -p no:cacheprovider'
TEST_FILE = "def test_add():\n    assert 1 + 1 == 2\n"


def call(name, **args):
    return {"name": name, "arguments": json.dumps(args)}


class ScriptedClient:
    """Plays replies through the streaming interface of openai.AsyncOpenAI."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
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


def make_client(replies):
    settings = AgentSettings(model="test")
    return TestClient(create_app([], settings, client=ScriptedClient(replies)))


def test_code_mode_runs_the_agent_and_streams_its_events(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))
    folder = tmp_path / "app"
    plan = call("plan", goal="Add", steps=["add"], criteria=["1 + 1 == 2"], verify_command=PYTEST)
    replies = [
        ("", [plan]),
        ("", [call("write_file", path="test_add.py", content=TEST_FILE)]),
        ("", [call("verify", note="done")]),
        ("", [call("verify", note="every criterion has its test")]),
        ("Hazır.", []),
    ]
    with make_client(replies) as client:
        assert client.get("/api/status").json()["code"] is True
        run = client.post("/api/runs", json={"mode": "code", "question": "Add things", "folder": str(folder)}).json()
        assert run["project"] == str(folder)
        events = read_events(client, run["id"])

    kinds = [e["kind"] for e in events]
    assert {"turn", "tool_call", "tool_result", "state", "outcome", "answer", "end"} <= set(kinds)
    assert events[-1] == {"kind": "end", "status": "done"}
    states = [e for e in events if e["kind"] == "state"]
    assert states[-1]["finished"] is True and states[0]["steps"] == [{"text": "add", "done": False}]
    assert next(e for e in events if e["kind"] == "outcome")["finished"] is True
    assert "Hazır." in next(e for e in events if e["kind"] == "answer")["html"]
    assert (folder / "test_add.py").is_file()


def test_code_mode_reports_a_stopped_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))
    replies = [("I will not use tools.", [])] * 8
    with make_client(replies) as client:
        run = client.post("/api/runs", json={"mode": "code", "question": "x", "folder": str(tmp_path / "p")}).json()
        events = read_events(client, run["id"])
    outcome = next(e for e in events if e["kind"] == "outcome")
    assert outcome["finished"] is False and "stopped before" in outcome["reason"]
    assert events[-1]["status"] == "stopped"


def test_code_mode_needs_an_absolute_folder(tmp_path):
    with make_client([]) as client:
        assert client.post("/api/runs", json={"mode": "code", "question": "x"}).status_code == 400
        assert client.post("/api/runs", json={"mode": "code", "question": "x", "folder": "relative"}).status_code == 400
        assert client.post("/api/runs", json={"question": ""}).status_code == 400  # research still needs a question


def test_pick_folder_returns_the_dialog_choice(monkeypatch, tmp_path):
    from local_ai.agent.web import app as web

    seen = []

    async def dialog(initial=""):
        seen.append(initial)
        return str(tmp_path)

    monkeypatch.setattr(web, "pick_folder_dialog", dialog)
    with make_client([]) as client:
        assert client.post("/api/pick-folder", json={"initial": "C:/x"}).json() == {"path": str(tmp_path)}
    assert seen == ["C:/x"]


def test_pick_folder_reports_a_dialog_that_cannot_open(monkeypatch):
    from local_ai.agent.web import app as web

    async def dialog(initial=""):
        raise RuntimeError("No module named 'tkinter'")

    monkeypatch.setattr(web, "pick_folder_dialog", dialog)
    with make_client([]) as client:
        response = client.post("/api/pick-folder", json={})
    assert response.status_code == 503 and "Type the path instead" in response.json()["error"]


def test_dialog_script_starts_with_tkinter():
    """The dialog itself needs a person; check that its script compiles and tkinter is importable."""
    import importlib.util

    from local_ai.agent.web.app import PICK_FOLDER

    compile(PICK_FOLDER, "pick_folder", "exec")
    assert importlib.util.find_spec("tkinter") is not None


def test_compact_button_endpoint(tmp_path):
    from local_ai.agent.web.app import Run

    class Agent:
        asked = 0

        def request_compact(self):
            self.asked += 1

    agent = Agent()
    app = create_app([], AgentSettings(model="test"), client=ScriptedClient([]))
    with TestClient(app) as client:
        app.state.runs["r1"] = Run(id="r1", question="q", project="p", status="running", agent=agent)
        app.state.runs["r2"] = Run(id="r2", question="q", project="p", status="done", agent=agent)
        app.state.runs["r3"] = Run(id="r3", question="q", project="p", status="running")  # a research run
        assert client.post("/api/runs/r1/compact").json() == {"ok": True} and agent.asked == 1
        assert client.post("/api/runs/r2/compact").status_code == 409
        assert client.post("/api/runs/r3/compact").status_code == 409
        assert client.post("/api/runs/none/compact").status_code == 404
    assert agent.asked == 1

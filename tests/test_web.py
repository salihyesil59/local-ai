"""Web UI API with a scripted model (no LM Studio)."""

import json

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.agent.config import AgentSettings, load_mcp_config  # noqa: E402
from local_ai.agent.web.app import create_app, parse_memory_lines  # noqa: E402
from test_loop import StreamingClient, reply  # noqa: E402


def script():
    return {
        "plan": [reply('{"language": "en", "sub_questions": [{"question": "What is c?", "needs_python": true}]}')],
        "research": [
            reply(calls=[("library__search_library", {"query": "speed of light"})]),
            reply(
                calls=[
                    (
                        "notebook__add_source",
                        {
                            "project": "light",
                            "source_id": "Speed of light",
                            "title": "Speed of light",
                            "origin": "wikipedia",
                            "quote": "c = 299792458 m/s",
                        },
                    )
                ]
            ),
            reply(calls=[("compute__python", {"code": "print(299792458 / 1000)"})]),
            reply("- c = 299792458 m/s [1], i.e. 299792.458 km/s."),
        ],
        "write": [reply("# Speed of light\n\nc = 299792458 m/s [1] = 299792.458 km/s.")],
        "learn": [reply('{"facts": [{"text": "c = 299792458 m/s", "cites": [1]}], "lessons": []}')],
    }


@pytest.fixture
def client(mcp_config):
    servers = load_mcp_config(mcp_config)
    settings = AgentSettings(model="test", critic=False, gap_check=False)
    app = create_app(servers, settings, client=StreamingClient(script()))
    with TestClient(app) as c:
        yield c


def read_events(client, run_id):
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["kind"] == "end":
                    break
    return events


def test_status_and_index(client):
    s = client.get("/api/status").json()
    assert s["error"] is None and {"notebook", "library", "compute"} <= set(s["servers"])
    assert "html" in s["formats"]
    page = client.get("/")
    assert page.status_code == 200 and "Research Agent" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_run_project_export_memory(client):
    run = client.post("/api/runs", json={"question": "What is the speed of light?", "project": "light"}).json()
    events = read_events(client, run["id"])
    kinds = [e["kind"] for e in events]
    assert kinds[-1] == "end" and events[-1]["status"] == "done", events[-3:]
    assert "plan" in kinds and "tool_call" in kinds and "findings" in kinds and "notebook" in kinds
    report = next(e for e in events if e["kind"] == "report")
    assert 'href="#ref-1"' in report["html"] and "<h1>Speed of light</h1>" in report["html"]
    # replaying a finished run returns the same events
    assert "token" in kinds and "stream_end" in kinds  # the UI streams model output
    replay = [e["kind"] for e in read_events(client, run["id"])]
    assert replay == [k for k in kinds if k not in ("token", "stream_end")]  # live output is not replayed

    projects = client.get("/api/projects").json()
    assert projects[0]["name"] == "light" and projects[0]["has_report"]
    detail = client.get("/api/projects/light").json()
    assert detail["sources"][0]["title"] == "Speed of light" and detail["notebook"] and detail["findings"]

    exported = client.post("/api/projects/light/export?format=html").json()
    page = client.get(exported["url"])
    assert page.status_code == 200 and "299792458" in page.text
    assert client.post("/api/projects/light/export?format=nope").status_code == 400
    assert client.get("/files/light/computations.ipynb").status_code == 200

    memories = client.get("/api/memory").json()
    assert memories and memories[0]["text"] == "c = 299792458 m/s"
    found = client.get("/api/memory?q=speed of light 299792458").json()
    assert found and found[0]["kind"] == "fact"
    assert client.delete(f"/api/memory/{memories[0]['id']}").json()["ok"]
    assert client.get("/api/memory").json() == []

    skills = client.get("/api/skills").json()
    assert any(s["name"] == "derivation" for s in skills)


def test_files_cannot_escape_workspace(client):
    assert client.get("/files/../../etc/passwd").status_code == 404
    assert client.get("/files/%2e%2e/%2e%2e/etc/passwd").status_code == 404
    assert client.get("/api/projects/..").status_code == 404


def test_validation(client):
    assert client.post("/api/runs", json={"question": "  "}).status_code == 400
    assert client.get("/api/runs/nope/events").status_code == 404


def test_parse_memory_lines():
    lines = "[m3] (fact) c is fast  — sources: Wikipedia\n[m4] (preference) Turkish\n(nothing)"
    assert parse_memory_lines(lines) == [
        {"id": 3, "kind": "fact", "text": "c is fast", "sources": "Wikipedia"},
        {"id": 4, "kind": "preference", "text": "Turkish", "sources": ""},
    ]

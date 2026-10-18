"""Web UI API with a scripted model (no LM Studio)."""

import json

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.core.mcp_bridge import load_mcp_config  # noqa: E402
from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app, parse_memory_lines  # noqa: E402
from test_loop import StreamingClient, reply  # noqa: E402


def script():
    return {
        "draft": [reply("A draft from memory.")],
        "plan": [reply('{"language": "en", "sub_questions": [{"question": "What is c?", "needs_python": true}]}')],
        "research": [
            reply(calls=[("library__search_library", {"query": "speed of light"})]),
            reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
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
    assert page.status_code == 200 and "Local AI" in page.text and page.headers["cache-control"] == "no-cache"
    # the frame: chats in the sidebar, the + menu and the model at the message box, the panel on the right
    for part in ('id="chat-rows"', 'id="plus-menu"', 'id="settings-toggle"', 'id="side-panel"', 'id="panel-toggle"'):
        assert part in page.text
    script = client.get("/static/app.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"  # a new version is used at once


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
    # a reply that streamed comes back as one event where it ended (with its text and thinking), not token by token
    assert replay == ["streamed" if k == "stream_end" else k for k in kinds if k != "token"]

    projects = client.get("/api/projects").json()
    assert projects[0]["name"] == "light" and projects[0]["has_report"]
    detail = client.get("/api/projects/light").json()
    assert detail["sources"][0]["title"] == "Speed of light" and detail["notebook"] and detail["findings"]

    # the card of a citation: the quote of source 1 with the tool output around it
    cited = client.get("/api/projects/light/sources/1/passages").json()
    assert cited["source"]["title"] == "Speed of light"
    (passage,) = cited["passages"]
    assert passage["quote"] == "c = 299792458 m/s" and passage["match"] == "c = 299792458 m/s"
    assert "Speed of light" in passage["before"] and "universal physical constant" in passage["after"]
    assert client.get("/api/projects/light/sources/99/passages").status_code == 404
    assert client.get("/api/projects/nope/sources/1/passages").status_code == 404

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


def test_find_passage_ignores_spacing_and_reports_a_missing_quote():
    from local_ai.web.app import find_passage

    evidence = "Intro text.\nThe speed of  light in vacuum,\nc, is exactly 299792458 metres per second. More."
    found = find_passage(evidence, "the speed of light in vacuum, c, is exactly 299792458 metres")
    assert found["match"].startswith("The speed of  light") and found["before"] == "Intro text.\n"
    assert found["after"].startswith(" per second")
    assert find_passage(evidence, "light is made of cheese") is None and find_passage(evidence, "…") is None


def test_mcp_servers_are_listed_added_disabled_and_removed(mcp_config):
    from local_ai.core.mcp_bridge import read_servers
    from local_ai.web.app import parse_mcp_server

    servers = load_mcp_config(mcp_config, only=["notebook"])
    app = create_app(servers, AgentSettings(model="test"), client=StreamingClient({}), config_path=mcp_config)
    library = read_servers(mcp_config)["library"]
    with TestClient(app) as client:
        listed = {s["name"]: s for s in client.get("/api/mcp").json()["servers"]}
        assert listed["notebook"]["connected"] and listed["notebook"]["required"] and listed["notebook"]["tools"]
        assert not listed["library"]["connected"]  # configured, but this UI started the notebook only

        # add one from the snippet of a README; it starts at once and its tools are there
        snippet = json.dumps({"mcpServers": {"books": library}})
        added = client.post("/api/mcp", json={"json": snippet}).json()
        assert added["name"] == "books" and added["connected"] and added["error"] == ""
        assert "search_library" in {t["name"] for t in added["tools"]}
        assert added["env"] == sorted(library["env"])  # the names of the variables, never their values
        assert "books" in client.get("/api/status").json()["servers"] and "books" in read_servers(mcp_config)

        # a command that does not start is saved and shown with its error
        bad = client.post("/api/mcp", json={"name": "bad", "command": "no-such-command-anywhere --x"}).json()
        assert not bad["connected"] and bad["error"] and read_servers(mcp_config)["bad"]["args"] == ["--x"]

        off = client.patch("/api/mcp/books", json={"disabled": True}).json()
        assert off["disabled"] and not off["connected"] and read_servers(mcp_config)["books"]["disabled"] is True
        on = client.patch("/api/mcp/books", json={"disabled": False}).json()
        assert on["connected"] and "disabled" not in read_servers(mcp_config)["books"]

        assert client.delete("/api/mcp/books").json() == {"ok": True}
        assert "books" not in read_servers(mcp_config) and "books" not in client.get("/api/status").json()["servers"]
        assert client.delete("/api/mcp/notebook").status_code == 400  # the research agent needs it
        assert client.patch("/api/mcp/notebook", json={"disabled": True}).status_code == 400
        assert client.delete("/api/mcp/nope").status_code == 404
        assert client.post("/api/mcp", json={"name": "bad name!", "command": "x"}).status_code == 400
        assert client.post("/api/mcp", json={"name": "x"}).status_code == 400

    assert parse_mcp_server({"name": "web", "url": "https://example.org/mcp"}) == ("web", {"url": "https://example.org/mcp"})
    assert parse_mcp_server({"name": "n", "json": '{"command": "uvx", "args": ["pkg"]}'})[1]["args"] == ["pkg"]
    assert parse_mcp_server({"name": "e", "command": "srv", "env": {"KEY": 1}})[1]["env"] == {"KEY": "1"}
    with TestClient(create_app([], AgentSettings(model="test"), client=StreamingClient({}))) as client:
        assert client.get("/api/mcp").json()["editable"] is False  # started without a file to change
        assert client.post("/api/mcp", json={"name": "x", "command": "y"}).status_code == 400


def test_a_research_with_chosen_servers_is_offered_add_source(mcp_config):
    # the page sends only the optional servers it offers; the notebook (add_source) is never among them
    model = StreamingClient(script())
    settings = AgentSettings(model="test", critic=False, gap_check=False)
    with TestClient(create_app(load_mcp_config(mcp_config), settings, client=model)) as c:
        body = {"question": "What is the speed of light?", "project": "light", "servers": ["library", "compute"]}
        events = read_events(c, c.post("/api/runs", json=body).json()["id"])
    offered = {t["function"]["name"] for t in model.requests["research"][0]["tools"]}
    assert "notebook__add_source" in offered and "library__search_library" in offered
    assert "[1] Speed of light" in next(e for e in events if e["kind"] == "report")["text"]


def test_a_planned_research_can_be_changed_before_it_starts(client, tmp_path):
    notes = tmp_path / "workspace" / "plan-me" / "notes"
    notes.mkdir(parents=True)
    plan = {"question": "Q", "language": "en", "sub_questions": [{"question": "A?", "search_terms_en": ["a"]}]}
    (notes / "plan.md").write_text(json.dumps(plan), encoding="utf-8")
    put = client.put("/api/projects/plan-me/plan", json={"sub_questions": ["A?", " New one ", ""]})
    assert put.json() == {"sub_questions": ["A?", "New one"]}
    saved = json.loads((notes / "plan.md").read_text(encoding="utf-8"))
    assert saved["sub_questions"] == [{"question": "A?", "search_terms_en": ["a"]}, {"question": "New one"}]
    assert client.put("/api/projects/plan-me/plan", json={"sub_questions": []}).status_code == 400
    assert client.put("/api/projects/nothing-here/plan", json={"sub_questions": ["x"]}).status_code == 404
    (notes / "finding-01.md").write_text("# A?\n\n- found", encoding="utf-8")
    assert client.put("/api/projects/plan-me/plan", json={"sub_questions": ["x"]}).status_code == 409


def test_the_answer_to_clarifying_questions_is_researched_with_them():
    from local_ai.web.runs import Run

    run = Run(id="r", question="Derive the field equations", project="")
    run.events = [{"kind": "user", "text": run.question}, {"kind": "clarify", "questions": ["Which signature?"]}]
    task = run.follow_up("(-,+,+,+), advanced")
    assert task.startswith("(-,+,+,+), advanced") and "Derive the field equations" in task and "Which signature?" in task


def test_a_stopped_research_keeps_what_it_found(mcp_config):
    from local_ai.web.runs import Run

    # what a later question is given: the findings of the stopped run, or what its tools returned
    run = Run(id="r", question="What is c?", project="", status="cancelled")
    run.events = [
        {"kind": "project", "name": "what-is-c"},
        {"kind": "tool_result", "name": "library__search_library", "output": "c = 299792458 m/s", "error": False},
        {"kind": "findings", "index": 1, "question": "What is c?", "text": "- c = 299792458 m/s [1]"},
        {"kind": "end", "status": "cancelled"},
    ]
    task = run.follow_up("And in km/s?")
    assert task.startswith("And in km/s?") and "stopped before it had an answer" in task
    assert "## What is c?\n- c = 299792458 m/s [1]" in task and run.resumable() == "what-is-c"
    assert run.summary()["resumable"] is True
    run.events = run.events[1:2]  # stopped before any finding: the tool output is what there is
    assert "c = 299792458 m/s" in run.follow_up("And?") and run.resumable() is None
    run.events += [{"kind": "answer", "text": "c is 299792458 m/s"}]
    assert "The answer was:\nc is 299792458 m/s" in run.follow_up("And?") and run.found_so_far() == ""

    # an empty message goes on with the stopped project: its saved plan and findings, then the report
    replies = script()
    replies["write"] *= 2
    replies["learn"] *= 2
    servers = load_mcp_config(mcp_config)
    app = create_app(servers, AgentSettings(model="test", critic=False, gap_check=False), client=StreamingClient(replies))
    with TestClient(app) as client:
        started = client.post("/api/runs", json={"question": "What is the speed of light?", "project": "light"}).json()
        events = read_events(client, started["id"])
        assert {"kind": "project", "name": "light"}.items() <= next(e for e in events if e["kind"] == "project").items()
        assert client.post(f"/api/runs/{started['id']}/messages", json={"question": ""}).status_code == 400  # it has its report

        stopped = app.state.runs[started["id"]]  # as if the user had stopped it before the report
        stopped.events = [e for e in stopped.events if e["kind"] not in ("report", "end", "notebook")]
        stopped.status = "cancelled"
        assert client.get("/api/runs").json()[0]["resumable"] is True
        assert client.post(f"/api/runs/{started['id']}/messages", json={"question": ""}).status_code == 202
        resumed = read_events(client, started["id"])
        kinds = [e["kind"] for e in resumed]
        assert kinds[-1] == "end" and resumed[-1]["status"] == "done"
        assert {"kind": "user", "text": "(continue the research)"}.items() <= resumed[kinds.index("user")].items()
        after = kinds[kinds.index("user") :]
        assert "report" in after and "subquestion" not in after  # the findings were there: only the report was written


"""Chats of the web UI: titles, folders and what survives a restart."""

import json

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_web import read_events  # noqa: E402
from test_web_code import PYTEST, ScriptedClient, call, own_workspace  # noqa: E402,F401

PLAN = call("plan", goal="Add", steps=["add"], criteria=["1 + 1 == 2"], verify_command=PYTEST)
TEST_FILE = "def test_add():\n    assert 1 + 1 == 2\n"
FINISHING = [
    ("", [{"name": "load_skill", "arguments": json.dumps({"name": "new-feature"})}]),
    ("", [PLAN]),
    ("", [call("write_file", path="test_add.py", content=TEST_FILE)]),
    ("", [call("verify", note="done")]),
    ("", [call("verify", note="test_add covers it")]),
    ("Hazır.", []),
]
# A follow-up message is a task of its own: a new plan, then verify until ALL DONE.
FOLLOW_UP = [
    ("", [call("plan", goal="Check", steps=["check add"], criteria=["1 + 1 == 2"], verify_command=PYTEST)]),
    ("", [call("verify", note="test_add in test_add.py covers it")]),
    ("", [call("verify", note="test_add in test_add.py covers 1 + 1 == 2")]),
    ("Yine hazır.", []),
]


def make_client(replies=()):
    return TestClient(create_app([], AgentSettings(model="test"), client=ScriptedClient(list(replies))))


def start_code_run(client, tmp_path, **extra):
    body = {"mode": "code", "question": "Add things", "folder": str(tmp_path / "app"), **extra}
    run = client.post("/api/runs", json=body).json()
    read_events(client, run["id"])  # wait for the end
    return run["id"]


def test_folders_can_be_created_renamed_and_deleted():
    with make_client() as client:
        assert client.post("/api/folders", json={"name": " "}).status_code == 400
        folder = client.post("/api/folders", json={"name": "Minisheet"}).json()
        assert client.get("/api/folders").json() == [folder]
        assert client.patch(f"/api/folders/{folder['id']}", json={"name": "Sheet"}).json() == {"ok": True}
        assert client.get("/api/folders").json()[0]["name"] == "Sheet"
        assert client.patch("/api/folders/nope", json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/folders/{folder['id']}").json() == {"ok": True}
        assert client.get("/api/folders").json() == []
        assert client.delete("/api/folders/nope").status_code == 404


def test_a_run_is_created_in_a_folder_and_can_be_titled_and_moved(tmp_path):
    with make_client(FINISHING) as client:
        a = client.post("/api/folders", json={"name": "A"}).json()["id"]
        b = client.post("/api/folders", json={"name": "B"}).json()["id"]
        bad = client.post("/api/runs", json={"mode": "code", "folder": str(tmp_path), "chat_folder": "nope"})
        assert bad.status_code == 400
        run_id = start_code_run(client, tmp_path, chat_folder=a)
        run = client.get("/api/runs").json()[0]
        assert run["folder"] == a and run["mode"] == "code" and run["title"] == "" and run["status"] == "done"

        renamed = client.patch(f"/api/runs/{run_id}", json={"title": "  Hesap tablosu  "}).json()
        assert renamed["title"] == "Hesap tablosu"
        assert client.patch(f"/api/runs/{run_id}", json={"folder": b}).json()["folder"] == b
        assert client.patch(f"/api/runs/{run_id}", json={"folder": None}).json()["folder"] is None
        assert client.patch(f"/api/runs/{run_id}", json={"folder": "nope"}).status_code == 400
        assert client.patch(f"/api/runs/{run_id}", json={"pinned": True}).json()["pinned"] is True
        assert client.get("/api/runs").json()[0]["pinned"] is True
        assert client.patch("/api/runs/none", json={"title": "x"}).status_code == 404

        client.patch(f"/api/runs/{run_id}", json={"folder": a})
        client.delete(f"/api/folders/{a}")  # its chats move out of it, they are not deleted
        assert client.get("/api/runs").json()[0]["folder"] is None


def test_chats_survive_a_restart(tmp_path):
    with make_client(FINISHING) as client:
        folder = client.post("/api/folders", json={"name": "Programs"}).json()["id"]
        run_id = start_code_run(client, tmp_path, chat_folder=folder)
        client.patch(f"/api/runs/{run_id}", json={"title": "My program"})

    with make_client() as client:  # a new server process on the same workspace
        assert client.get("/api/folders").json() == [{"id": folder, "name": "Programs"}]
        (run,) = client.get("/api/runs").json()
        assert (run["id"], run["title"], run["folder"], run["status"]) == (run_id, "My program", folder, "done")
        events = read_events(client, run_id)
        kinds = [e["kind"] for e in events]
        assert {"turn", "tool_call", "state", "outcome", "answer"} <= set(kinds) and kinds[-1] == "end"
        assert "token" not in kinds  # live output is not kept


def test_a_run_cut_off_by_a_restart_is_marked_interrupted(tmp_path):
    with make_client() as client:
        store = client.app.state.store
        store.add_run("r1", question="q", project="p", mode="code", status="running")
        store.append_event("r1", {"kind": "log", "text": "started"})
    with make_client() as client:
        assert client.get("/api/runs").json()[0]["status"] == "interrupted"
        events = read_events(client, "r1")
        assert [e["kind"] for e in events] == ["log", "end"] and events[-1]["status"] == "interrupted"
        assert json.loads((tmp_path / "ws" / "ui" / "index.json").read_text())["runs"]["r1"]["status"] == "interrupted"


def test_runs_can_be_deleted_but_not_while_running(tmp_path):
    with make_client(FINISHING) as client:
        run_id = start_code_run(client, tmp_path)
        log = tmp_path / "ws" / "ui" / "runs" / f"{run_id}.jsonl"
        assert log.is_file()
        app = client.app
        app.state.runs[run_id].status = "running"
        assert client.delete(f"/api/runs/{run_id}").status_code == 409
        app.state.runs[run_id].status = "done"
        assert client.delete(f"/api/runs/{run_id}").json() == {"ok": True}
        assert client.get("/api/runs").json() == [] and not log.exists()
        assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_a_follow_up_message_continues_the_same_chat(tmp_path):
    more = FOLLOW_UP
    with make_client([*FINISHING, *more]) as client:
        run_id = start_code_run(client, tmp_path)
        assert client.post("/api/runs/nope/messages", json={"question": "x"}).status_code == 404
        assert client.post(f"/api/runs/{run_id}/messages", json={"question": "Check it again"}).status_code == 202
        events = read_events(client, run_id)  # the whole chat: the first message, then the follow-up
        kinds = [e["kind"] for e in events]
        assert kinds.count("segment_end") == 1 and kinds[-1] == "end" and events[-1]["status"] == "done"
        assert [e["text"] for e in events if e["kind"] == "user"] == ["Check it again"]
        assert [e["text"] for e in events if e["kind"] == "answer"] == ["Hazır.", "Yine hazır."]
        assert kinds.index("user") > kinds.index("segment_end")
        assert len(client.get("/api/runs").json()) == 1  # no new chat

        run = client.app.state.runs[run_id]
        assert "Check it again" in run.follow_up("And now?") and "Yine hazır." in run.follow_up("And now?")
        # while a code run works, a message steers its agent; without an agent the chat is busy
        run.status = "running"
        assert client.post(f"/api/runs/{run_id}/messages", json={"question": "Use tabs"}).status_code == 202
        assert run.agent.steering == ["Use tabs"] and run.events[-1] == {"kind": "steer", "text": "Use tabs"}
        run.agent = None
        assert client.post(f"/api/runs/{run_id}/messages", json={"question": "x"}).status_code == 409
        run.status = "done"

        # the file tree of the project and the preview of a file
        listed = client.get(f"/api/runs/{run_id}/files").json()
        assert listed["files"] == ["test_add.py"] and listed["more"] == 0  # .local-coder is not listed
        shown = client.get(f"/api/runs/{run_id}/file", params={"path": "test_add.py"}).json()
        assert shown["content"].startswith("def test_add")
        assert client.get(f"/api/runs/{run_id}/file", params={"path": "../../etc/passwd"}).status_code == 404
        assert client.get(f"/api/runs/{run_id}/file", params={"path": "nope.py"}).status_code == 404
        assert client.get("/api/runs/nope/files").status_code == 404


def test_a_chat_keeps_its_settings(tmp_path):
    from local_ai.web.runs import clean_settings

    assert clean_settings({"model": " m ", "temperature": "0.3", "max_tokens": 10, "max_turns": 40, "x": 1}) == {
        "model": "m",
        "temperature": 0.3,
        "max_turns": 40,  # max_tokens is out of range, x is unknown
    }
    assert clean_settings(None) == {}

    more = FOLLOW_UP
    with make_client([*FINISHING, *more]) as client:
        chosen = {"temperature": 0.2, "max_turns": 30, "context_chars": 80_000}
        run_id = start_code_run(client, tmp_path, settings=chosen)
        assert client.get("/api/runs").json()[0]["settings"] == chosen
        turn = next(e for e in read_events(client, run_id) if e["kind"] == "turn")
        assert turn["max_turns"] == 30 and turn["context_limit"] == 80_000
        client.post(f"/api/runs/{run_id}/messages", json={"question": "Again", "settings": {"max_turns": 12}})
        assert [e["max_turns"] for e in read_events(client, run_id) if e["kind"] == "turn"][-1] == 12
        assert client.get("/api/models").json()["models"] == []  # the scripted client lists none; the page still works

    with make_client() as client:  # after a restart
        assert client.get("/api/runs").json()[0]["settings"] == {"max_turns": 12}


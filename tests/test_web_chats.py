"""Chats of the web UI: titles, folders and what survives a restart."""

import json

import pytest

pytest.importorskip("httpx")
pytest.importorskip("local_coder")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.agent.config import AgentSettings  # noqa: E402
from local_ai.agent.web.app import create_app  # noqa: E402
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

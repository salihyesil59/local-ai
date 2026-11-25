"""Chats of the web UI: titles, folders and what survives a restart."""

import json

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_web import read_events  # noqa: E402
from web_helpers import ScriptedClient, own_workspace  # noqa: E402,F401

FINISHING = [("Hazır.", [])]
FOLLOW_UP = [("Yine hazır.", [])]


def make_client(replies=()):
    return TestClient(create_app([], AgentSettings(model="test"), client=ScriptedClient(list(replies))))


def start_chat(client, **extra):
    body = {"mode": "chat", "question": "Add things", **extra}
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


def test_a_run_is_created_in_a_folder_and_can_be_titled_and_moved():
    with make_client(FINISHING) as client:
        a = client.post("/api/folders", json={"name": "A"}).json()["id"]
        b = client.post("/api/folders", json={"name": "B"}).json()["id"]
        bad = client.post("/api/runs", json={"mode": "chat", "question": "x", "chat_folder": "nope"})
        assert bad.status_code == 400
        run_id = start_chat(client, chat_folder=a)
        run = client.get("/api/runs").json()[0]
        assert run["folder"] == a and run["mode"] == "chat" and run["title"] == "" and run["status"] == "done"

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


def test_chats_survive_a_restart():
    with make_client(FINISHING) as client:
        folder = client.post("/api/folders", json={"name": "Programs"}).json()["id"]
        run_id = start_chat(client, chat_folder=folder)
        client.patch(f"/api/runs/{run_id}", json={"title": "My program"})

    with make_client() as client:  # a new server process on the same workspace
        assert client.get("/api/folders").json() == [{"id": folder, "name": "Programs"}]
        (run,) = client.get("/api/runs").json()
        assert (run["id"], run["title"], run["folder"], run["status"]) == (run_id, "My program", folder, "done")
        events = read_events(client, run_id)
        kinds = [e["kind"] for e in events]
        assert {"turn", "streamed", "answer"} <= set(kinds) and kinds[-1] == "end"
        assert "token" not in kinds  # live output is not kept


def test_a_run_cut_off_by_a_restart_is_marked_interrupted(tmp_path):
    with make_client() as client:
        store = client.app.state.store
        store.add_run("r1", question="q", project="p", mode="chat", status="running")
        store.append_event("r1", {"kind": "log", "text": "started"})
    with make_client() as client:
        assert client.get("/api/runs").json()[0]["status"] == "interrupted"
        events = read_events(client, "r1")
        assert [e["kind"] for e in events] == ["log", "end"] and events[-1]["status"] == "interrupted"
        assert json.loads((tmp_path / "ws" / "ui" / "index.json").read_text())["runs"]["r1"]["status"] == "interrupted"


def test_a_code_chat_of_an_earlier_version_can_be_read_but_not_continued():
    with make_client() as client:
        client.app.state.store.add_run("r1", question="q", project="C:/app", mode="code", status="done")
    with make_client() as client:
        assert client.get("/api/runs").json()[0]["mode"] == "code"
        assert read_events(client, "r1")[-1]["kind"] == "end"
        assert client.post("/api/runs/r1/messages", json={"question": "Go on"}).status_code == 400


def test_runs_can_be_deleted_but_not_while_running(tmp_path):
    with make_client(FINISHING) as client:
        run_id = start_chat(client)
        log = tmp_path / "ws" / "ui" / "runs" / f"{run_id}.jsonl"
        assert log.is_file()
        app = client.app
        app.state.runs[run_id].status = "running"
        assert client.delete(f"/api/runs/{run_id}").status_code == 409
        app.state.runs[run_id].status = "done"
        assert client.delete(f"/api/runs/{run_id}").json() == {"ok": True}
        assert client.get("/api/runs").json() == [] and not log.exists()
        assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_a_page_that_opens_a_working_chat_sees_the_reply_from_its_start():
    from local_ai.web.runs import Run

    run = Run(id="r", question="q", project="", mode="chat", status="running")
    run.push({"kind": "user", "text": "q"})
    run.push({"kind": "token", "text": "Let me think. ", "thinking": True})
    run.push({"kind": "token", "text": "The ans", "thinking": False})
    run.push({"kind": "token", "text": "wer is", "thinking": False})
    # opened now: the reply so far, as one piece of thinking and one of text
    assert run.live_events() == [
        {"kind": "token", "index": None, "text": "Let me think. ", "thinking": True},
        {"kind": "token", "index": None, "text": "The answer is", "thinking": False},
    ]
    assert [e["kind"] for e in run.events] == ["user"]  # tokens are not events of their own
    run.push({"kind": "stream_end"})
    assert run.live_events() == []
    assert run.events[-1] == {"kind": "streamed", "index": None, "text": "The answer is", "thinking": "Let me think. "}


def test_a_finished_chat_opened_later_shows_what_the_model_thought_and_wrote():
    with make_client([("Hazır cevap.", [])]) as client:
        run = client.post("/api/runs", json={"mode": "chat", "question": "Merhaba"}).json()
        read_events(client, run["id"])
        later = read_events(client, run["id"])  # a page that opens the chat now
    [streamed] = [e for e in later if e["kind"] == "streamed"]
    assert streamed["text"] == "Hazır cevap." and later.index(streamed) < [e["kind"] for e in later].index("answer")


def test_a_follow_up_message_continues_the_same_chat():
    with make_client([*FINISHING, *FOLLOW_UP]) as client:
        run_id = start_chat(client)
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
        # while a chat works, a message waits for the reply; without an agent the chat is busy
        run.status = "running"
        assert client.post(f"/api/runs/{run_id}/messages", json={"question": "Use tabs"}).status_code == 202
        assert run.agent.steering == [("Use tabs", [])] and run.events[-1] == {"kind": "steer", "text": "Use tabs"}
        run.agent = None
        assert client.post(f"/api/runs/{run_id}/messages", json={"question": "x"}).status_code == 409
        run.status = "done"


def test_a_chat_keeps_its_settings():
    from local_ai.web.runs import clean_settings

    assert clean_settings({"model": " m ", "temperature": "0.3", "max_tokens": 10, "max_steps": 40, "x": 1}) == {
        "model": "m",
        "temperature": 0.3,
        "max_steps": 40,  # max_tokens is out of range, x is unknown
    }
    assert clean_settings(None) == {}
    assert clean_settings({"max_thinking_chars": "20000", "context_chars": 5}) == {"max_thinking_chars": 20000}

    with make_client([*FINISHING, *FOLLOW_UP]) as client:
        chosen = {"temperature": 0.2, "context_chars": 80_000}
        run_id = start_chat(client, settings=chosen)
        assert client.get("/api/runs").json()[0]["settings"] == chosen
        turn = next(e for e in read_events(client, run_id) if e["kind"] == "turn")
        assert turn["context_limit"] == 80_000
        client.post(f"/api/runs/{run_id}/messages", json={"question": "Again", "settings": {"context_chars": 50_000}})
        assert [e["context_limit"] for e in read_events(client, run_id) if e["kind"] == "turn"][-1] == 50_000
        assert client.get("/api/models").json()["models"] == []  # the scripted client lists none; the page still works

    with make_client() as client:  # after a restart
        assert client.get("/api/runs").json()[0]["settings"] == {"context_chars": 50_000}


def test_a_chat_keeps_its_conversation_with_the_model():
    """A follow-up goes on with everything that was said, also after the UI was restarted."""
    with make_client(FINISHING) as client:
        run_id = start_chat(client)
        first = client.app.state.store.load_messages(run_id)
        assert first["messages"][1] == {"role": "user", "content": "Add things"}
        assert first["messages"][-1] == {"role": "assistant", "content": "Hazır."}

    with make_client(FOLLOW_UP) as client:  # a new server process: the conversation comes from the store
        client.post(f"/api/runs/{run_id}/messages", json={"question": "Check it again"})
        assert read_events(client, run_id)[-1] == {"kind": "end", "status": "done"}
        messages = client.app.state.store.load_messages(run_id)["messages"]
        tasks = [m["content"] for m in messages if m["role"] == "user"]
        assert tasks == ["Add things", "Check it again"]  # the conversation itself is there, no retelling needed

        assert client.delete(f"/api/runs/{run_id}").json() == {"ok": True}
        assert client.app.state.store.load_messages(run_id) == {}

"""The Offline view: library files, notebooks and ZIM archives, and a chat that uses a notebook."""

import json
import time

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web import offline  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_web import read_events  # noqa: E402
from test_web_code import ScriptedClient, own_workspace  # noqa: E402,F401


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / "library"
    monkeypatch.setenv("LOCAL_AI_LIBRARY", str(folder))
    monkeypatch.setenv("LOCAL_AI_ZIM_DIR", str(tmp_path / "zim"))
    (tmp_path / "zim").mkdir()
    (tmp_path / "zim" / "wikipedia_en_physics_2026-01.zim").write_bytes(b"zim")
    return folder


def test_the_places_come_from_the_library_server_of_the_mcp_json(tmp_path):
    config = tmp_path / "mcp.json"
    env = {"LOCAL_AI_LIBRARY": str(tmp_path / "mine"), "LOCAL_AI_ZIM_DIR": str(tmp_path / "z")}
    config.write_text(json.dumps({"mcpServers": {"library": {"command": "x", "env": env}}}), encoding="utf-8")
    places = offline.places(config)
    assert places.library == tmp_path / "mine" and places.zim_dirs == [tmp_path / "z"]


def test_the_index_button_indexes_the_library_in_the_background(library, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_EMBED_BACKEND", "hash")
    library.mkdir()
    (library / "friedmann.md").write_text("# Friedmann\n\nThe scale factor obeys the Friedmann equations.", "utf-8")
    with TestClient(create_app([], AgentSettings(model="test"), client=ScriptedClient([]))) as ui:
        assert ui.post("/api/offline/index").status_code == 202
        for _ in range(100):
            state = ui.get("/api/offline").json()
            if not state["index"]["running"]:
                break
            time.sleep(0.05)
        job = state["index"]
        assert job["error"] == "" and job["result"]["indexed"] == 1 and job["embeddings"] is True
        assert state["library"]["files"][0]["indexed"]
        (library / "friedmann.md").unlink()
        assert ui.post("/api/offline/index").status_code == 202
        for _ in range(100):
            job = ui.get("/api/offline").json()["index"]
            if not job["running"]:
                break
            time.sleep(0.05)
        assert job["result"]["removed"] == 1


def test_files_and_notebooks_are_managed_in_the_library(library):
    with TestClient(create_app([], AgentSettings(model="test"), client=ScriptedClient([]))) as ui:
        assert ui.get("/api/offline").json()["library"] == {
            "dir": str(library), "exists": False, "files": [], "notebooks": [], "more": 0
        }
        added = ui.post("/api/offline/files", params={"name": "notes.md"}, content=b"# Notes").json()
        assert added["path"] == "notes.md" and (library / "notes.md").read_bytes() == b"# Notes"
        again = ui.post("/api/offline/files", params={"name": "notes.md"}, content=b"# Two").json()
        assert again["path"] == "notes (2).md"  # a name that is taken gets a number
        assert ui.post("/api/offline/files", params={"name": "run.exe"}, content=b"x").status_code == 400
        assert ui.post("/api/offline/files", params={"name": "a.md", "folder": "../out"}, content=b"x").status_code == 400
        assert ui.post("/api/offline/files", params={"name": "a.md"}, content=b"").status_code == 400

        assert ui.post("/api/offline/notebooks", json={"name": "Kozmoloji Notları"}).json() == {"name": "kozmoloji-notlari"}
        assert ui.post("/api/offline/notebooks", json={"name": "kozmoloji notlari"}).status_code == 400  # exists
        params = {"name": "friedmann.pdf", "folder": "notebooks/kozmoloji-notlari"}
        assert ui.post("/api/offline/files", params=params, content=b"%PDF-1.4").status_code == 201

        state = ui.get("/api/offline").json()
        assert [f["path"] for f in state["library"]["files"]] == ["notes (2).md", "notes.md"]
        (book,) = state["library"]["notebooks"]
        assert book["name"] == "kozmoloji-notlari" and [f["path"] for f in book["files"]] == [
            "notebooks/kozmoloji-notlari/friedmann.pdf"
        ]
        assert not book["files"][0]["indexed"]
        assert state["zim"]["archives"][0]["name"] == "wikipedia_en_physics" and state["job"]["running"] is False

        assert ui.delete("/api/offline/files", params={"path": "notes (2).md"}).json() == {"ok": True}
        assert ui.delete("/api/offline/files", params={"path": "../mcp.json"}).status_code == 404
        assert ui.delete("/api/offline/notebooks/kozmoloji-notlari").json() == {"ok": True}
        assert ui.delete("/api/offline/notebooks/Not%20One").status_code == 404
        assert not (library / "notebooks" / "kozmoloji-notlari").exists()


def test_a_chat_with_a_notebook_is_told_to_answer_from_its_files(library):
    client = ScriptedClient([("Friedmann denklemi...", [])])
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        ui.post("/api/offline/notebooks", json={"name": "cosmo"})
        ui.post("/api/offline/files", params={"name": "friedmann.md", "folder": "notebooks/cosmo"}, content=b"H^2")
        body = {"mode": "chat", "question": "Friedmann denklemi nedir?", "notebook": "cosmo"}
        run = ui.post("/api/runs", json=body).json()
        events = read_events(ui, run["id"])
        assert run["notebook"] == "cosmo" and any(e.get("text") == "Notebook: cosmo (1 files)" for e in events)
        sent = client.requests[0]["messages"][-1]["content"]
        assert sent.startswith("Friedmann denklemi nedir?") and 'notebook "cosmo"' in sent and "friedmann.md" in sent

        gone = ui.post("/api/runs", json={**body, "notebook": "deleted"}).json()
        events = read_events(ui, gone["id"])
        assert events[-1]["status"] == "failed" and "No notebook" in events[-2]["text"]


def test_a_zim_update_downloads_the_new_release_and_removes_the_old(tmp_path, monkeypatch):
    import time

    old = tmp_path / "wikipedia_en_physics_2026-01.zim"
    old.write_bytes(b"old")

    def download(url, dest, quiet=False, progress=None):
        progress(1, 2)
        dest.write_bytes(b"new")
        return dest

    monkeypatch.setattr(offline.data, "download", download)
    job = offline.ZimJob()
    update = {"file": old.name, "folder": str(tmp_path), "new_file": "wikipedia_en_physics_2026-10.zim",
              "url": "https://download.kiwix.org/x.zim", "size": 2}  # fmt: skip
    job.start([update], keep_old=False)
    for _ in range(100):
        if not job.state["running"]:
            break
        time.sleep(0.05)
    assert job.state["log"] == [f"{old.name} -> wikipedia_en_physics_2026-10.zim"] and not old.exists()
    assert (tmp_path / "wikipedia_en_physics_2026-10.zim").read_bytes() == b"new"

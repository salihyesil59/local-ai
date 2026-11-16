"""What a code run changed in a git project, file by file, and the Changes panel's endpoints that show and undo it."""

import subprocess

import pytest

from local_ai.coder import changes

pytest.importorskip("httpx")
from test_web import read_events  # noqa: E402
from test_web_code import call, make_client  # noqa: E402


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "mine.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "start")
    return root


def test_changes_lists_each_file_with_its_diff(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 2\nz = 3\n", encoding="utf-8")
    (repo / "mine.py").unlink()
    (repo / ".local-coder").mkdir()
    (repo / ".local-coder" / "state.json").write_text("{}", encoding="utf-8")

    found = {f["path"]: f for f in changes.changes(repo, {"mine.py"})}
    assert set(found) == {"calc.py", "new.py", "mine.py"}  # the coder's own folder is left out
    assert found["calc.py"]["status"] == "modified" and (found["calc.py"]["added"], found["calc.py"]["removed"]) == (
        1,
        1,
    )
    assert "+    return b + a" in found["calc.py"]["diff"]
    assert found["new.py"]["status"] == "added" and found["new.py"]["added"] == 2
    assert found["mine.py"]["status"] == "deleted" and found["mine.py"]["before_run"] is True
    assert found["calc.py"]["before_run"] is False


def test_undo_restores_deletes_and_keeps_the_users_work(repo):
    (repo / "calc.py").write_text("broken\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 2\n", encoding="utf-8")
    (repo / "mine.py").write_text("x = 2\n", encoding="utf-8")

    assert changes.undo(repo, "calc.py", {"mine.py"}) == ""
    assert (repo / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert changes.undo(repo, "new.py", {"mine.py"}) == "" and not (repo / "new.py").exists()
    assert "already changed before the run" in changes.undo(repo, "mine.py", {"mine.py"})
    assert (repo / "mine.py").read_text(encoding="utf-8") == "x = 2\n"
    assert "no change to undo" in changes.undo(repo, "calc.py", set())
    assert "outside the project folder" in changes.undo(repo, "../elsewhere.py", set())


def test_a_folder_without_git_has_no_changes(tmp_path):
    assert changes.is_repo(tmp_path) is False


def test_changes_panel_endpoints(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CODER_HOME", str(tmp_path / "home"))
    (repo / "mine.py").write_text("x = 2\n", encoding="utf-8")  # the user's own change, before the chat
    replies = [
        ("", [call("write_file", path="new.py", content="y = 2\n")]),
        ("", [call("edit_file", path="calc.py", old="a + b", new="b + a")]),
        ("Hazır.", []),
    ]
    with make_client(replies) as client:
        body = {"mode": "code", "question": "Change things", "folder": str(repo), "quick": True}
        run = client.post("/api/runs", json=body).json()
        read_events(client, run["id"])

        listed = client.get(f"/api/runs/{run['id']}/changes").json()
        assert listed["git"] is True and listed["running"] is False
        found = {f["path"]: f for f in listed["files"]}
        assert set(found) == {"new.py", "calc.py", "mine.py"}
        assert found["mine.py"]["before_run"] is True and found["calc.py"]["before_run"] is False

        undo = f"/api/runs/{run['id']}/changes/undo"
        assert client.post(undo, json={"path": "mine.py"}).status_code == 409
        assert client.post(undo, json={"path": "calc.py"}).json() == {"ok": True}
        assert client.post(undo, json={"path": "new.py"}).json() == {"ok": True}
        assert "a + b" in (repo / "calc.py").read_text(encoding="utf-8") and not (repo / "new.py").exists()
        assert (repo / "mine.py").read_text(encoding="utf-8") == "x = 2\n"

        plain = client.post("/api/runs", json={**body, "folder": str(tmp_path / "plain")}).json()
        read_events(client, plain["id"])
        assert client.get(f"/api/runs/{plain['id']}/changes").json() == {"git": False, "files": []}

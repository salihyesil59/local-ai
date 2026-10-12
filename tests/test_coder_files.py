import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import PYTEST
from local_ai.coder import files as fl
from local_ai.coder.workflow import ALL_DONE


def test_write_read_and_list(project):
    assert fl.write_file(project, "pkg/mod.py", "a = 1\nb = 2\nc = 3\n") == "Wrote pkg/mod.py (3 lines)."
    assert fl.read_file(project, "pkg/mod.py") == "a = 1\nb = 2\nc = 3"
    assert fl.read_file(project, "pkg/mod.py", start=2, end=2) == "b = 2"
    assert fl.read_file(project, "missing.py").startswith("Error: cannot read")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    assert fl.list_files(project) == "pkg/\npkg/mod.py"
    assert fl.list_files(project, "pkg/mod.py").startswith("Error")


def test_paths_cannot_leave_the_project(project):
    assert "outside the project" in fl.write_file(project, "../evil.py", "x")
    assert "outside the project" in fl.read_file(project, str(project.parent / "other.txt"))
    assert not (project.parent / "evil.py").exists()
    assert "belongs to the coder tools" in fl.write_file(project, ".local-coder/state.json", "{}")
    # an absolute path inside the project is fine
    assert fl.write_file(project, str(project / "ok.py"), "x = 1\n").startswith("Wrote")


def test_edit_needs_one_exact_match(project):
    fl.write_file(project, "m.py", "x = 1\ny = 1\n")
    assert "not found" in fl.edit_file(project, "m.py", "z = 1", "z = 2")
    assert "appears 2 times" in fl.edit_file(project, "m.py", "= 1", "= 2")
    assert fl.edit_file(project, "m.py", "x = 1", "x = 5") == "Edited m.py."
    assert fl.read_file(project, "m.py") == "x = 5\ny = 1"
    assert fl.edit_file(project, "m.py", "", "x").startswith("Error")


def test_edit_keeps_windows_line_endings(project):
    (project / "w.py").write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    assert fl.edit_file(project, "w.py", "a = 1\nb = 2", "a = 9\nb = 8") == "Edited w.py."
    assert (project / "w.py").read_bytes() == b"a = 9\r\nb = 8\r\nc = 3\r\n"


def test_long_file_is_cut(project):
    fl.write_file(project, "big.txt", "x" * (fl.MAX_READ + 10))
    assert "cut at" in fl.read_file(project, "big.txt")


@pytest.mark.anyio
async def test_stdio_server_with_files(project):
    env = {**os.environ, "LOCAL_CODER_HOME": os.environ["LOCAL_CODER_HOME"]}
    params = StdioServerParameters(command=sys.executable, args=["-m", "local_ai.coder", "--files"], env=env)

    async def call(session, name, **arguments):
        result = await session.call_tool(name, arguments)
        return "\n".join(item.text for item in result.content if item.type == "text")

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert names == {"plan", "verify", "status", "read_file", "write_file", "edit_file", "list_files"}

        await call(
            session, "plan", goal="g", steps=["only"], criteria=["add(1, 1) == 2"], verify_command=PYTEST,
            project=str(project),
        )  # fmt: skip
        await call(session, "write_file", path="test_add.py", content="def test_add():\n    assert 1 + 1 == 3\n")
        assert (await call(session, "verify")).startswith("FAILED")
        await call(session, "edit_file", path="test_add.py", old="== 3", new="== 2")
        assert "REVIEW before you finish" in await call(session, "verify", note="fixed")
        assert ALL_DONE in await call(session, "verify", note="test_add covers it")
        assert await call(session, "list_files") == "test_add.py"

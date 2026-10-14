import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import PYTEST
from local_ai.coder import files as fl
from local_ai.coder.tools import coder_tools
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
        files = {"read_file", "write_file", "edit_file", "list_files", "search", "repo_map", "outline"}
        assert names == {"plan", "verify", "status"} | files

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


def test_run_python_shows_what_the_code_does(project):
    (project / "shapes.py").write_text("def area(r):\n    return 3 * r * r\n", encoding="utf-8")
    run = coder_tools(project)["run_python"].fn
    assert run("import shapes\nprint(shapes.area(2), '⎠')").strip() == "12 ⎠"  # the project is importable
    failed = run("import shapes\nshapes.area()")
    assert failed.startswith("The script failed.") and "TypeError" in failed and "line 2" in failed


EXISTING = r'''from shapes import area

x = 1


def test_area():
    expected = """\
12
"""
    assert str(area(2)) + "\n" == expected


def test_zero():
    assert area(0) == 0
'''


def test_existing_tests_are_kept_once(project):
    from local_ai.coder import state as st

    (project / "tests").mkdir()
    (project / "tests" / "test_shapes.py").write_text(EXISTING, encoding="utf-8")
    st.save(project, st.new_state("g", ["s"], ["c"], PYTEST))
    tools = {name: t.fn for name, t in coder_tools(project).items()}
    path = "tests/test_shapes.py"

    added = tools["edit_file"](path, "    assert area(0) == 0\n", "    assert area(0) == 0\n\n\ndef test_one():\n    assert area(1) == 3\n")
    assert added == f"Edited {path}."  # adding a test is fine
    refused = tools["edit_file"](path, "12\n", "13\n")
    assert refused.startswith("Error: not changed") and "test_area" in refused and "test_zero" not in refused
    assert tools["edit_file"](path, "12\n", "13\n") == f"Edited {path}."  # asked again: the task wants it

    (project / "tests" / "test_more.py").write_text(EXISTING, encoding="utf-8")
    replaced = tools["write_file"]("tests/test_more.py", "def test_mine():\n    pass\n")
    assert replaced.startswith("Error: not changed") and "test_area, test_zero" in replaced
    assert tools["write_file"]("tests/test_new.py", "def test_mine():\n    pass\n").startswith("Wrote")
    assert tools["edit_file"]("tests/test_new.py", "pass", "assert 1") == "Edited tests/test_new.py."  # its own


def test_replace_in_files_renames_in_one_call(project):
    (project / "pkg").mkdir()
    (project / "pkg" / "calc.py").write_bytes(b"def total(xs):\r\n    subtotal = 0\r\n    return total_of(xs)\r\n")
    (project / "pkg" / "use.py").write_text("from pkg.calc import total\nprint(total([1]))\n", encoding="utf-8")
    (project / "notes.md").write_text("total is used in calc\n", encoding="utf-8")
    (project / "logo.png").write_bytes(b"\x89PNG\x00\xff total")

    out = fl.replace_in_files(project, "total", "grand_total", glob="*.py", word=True)
    assert out.splitlines() == ["Replaced 3 occurrences in 2 files:", "  pkg/calc.py: 1", "  pkg/use.py: 2"]
    calc = (project / "pkg" / "calc.py").read_bytes()
    assert calc == b"def grand_total(xs):\r\n    subtotal = 0\r\n    return total_of(xs)\r\n"  # words only, CRLF kept
    assert (project / "notes.md").read_text(encoding="utf-8") == "total is used in calc\n"  # not a *.py file

    assert "Replaced 1" in fl.replace_in_files(project, r"def (\w+)\(", r"def \1_v2(", path="pkg/calc.py", regex=True)
    assert b"def grand_total_v2(xs)" in (project / "pkg" / "calc.py").read_bytes()
    assert fl.replace_in_files(project, "nothing_like_it", "x").startswith("No match")
    assert fl.replace_in_files(project, "(", "x", regex=True).startswith("Error")
    assert fl.replace_in_files(project, "a", "b", path="../outside").startswith("Error")

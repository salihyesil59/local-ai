"""The check of a changed Python file right after an edit, and the edit tools that show its result."""

import pytest

from local_ai.coder import lint
from local_ai.coder import state as st
from local_ai.coder.tools import coder_tools

PYTEST = "python -m pytest -q"


@pytest.fixture(params=["ruff", "compile"])
def checker(request, monkeypatch):
    """Each test runs with ruff and with the fallback that only compiles."""
    if request.param == "ruff":
        if lint.ruff_command() is None:
            pytest.skip("ruff is not installed")
    else:
        monkeypatch.setattr(lint, "ruff_command", lambda: None)
    return request.param


def test_syntax_error_is_reported(tmp_path, checker):
    (tmp_path / "a.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    found = lint.check(tmp_path, ["a.py"])
    assert "a.py:1:" in found


def test_undefined_name_is_reported_by_ruff(tmp_path, checker):
    (tmp_path / "a.py").write_text("def f():\n    return missing_name\n", encoding="utf-8")
    found = lint.check(tmp_path, ["a.py"])
    if checker == "ruff":
        assert "a.py:2:" in found and "missing_name" in found
    else:
        assert found == ""  # compiling finds syntax errors only


def test_clean_and_non_python_files_give_nothing(tmp_path, checker):
    (tmp_path / "a.py").write_text("import os\n\nprint(os.sep)\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("this is not python (\n", encoding="utf-8")
    assert lint.check(tmp_path, ["a.py", "b.js"]) == ""
    assert lint.check(tmp_path, []) == ""


def test_edit_tools_add_the_problems(tmp_path, checker):
    st.save(tmp_path, st.new_state("g", ["s"], ["c"], PYTEST))
    tools = {name: t.fn for name, t in coder_tools(tmp_path, require_skill=False).items()}

    clean = tools["write_file"]("calc.py", "def add(a, b):\n    return a + b\n")
    assert clean == "Wrote calc.py (2 lines)."
    broken = tools["edit_file"]("calc.py", "    return a + b\n", "    return a +\n")
    assert broken.startswith("Edited calc.py.") and "Problems found" in broken and "calc.py:2:" in broken
    fixed = tools["edit_file"]("calc.py", "    return a +\n", "    return a + b\n")
    assert fixed == "Edited calc.py."

    (tmp_path / "other.py").write_text("x = add(1, 2)\n", encoding="utf-8")
    replaced = tools["replace_in_files"]("add(1, 2)", "add(1, 2", glob="*.py")
    assert "Problems found" in replaced and "\nother.py:" in replaced
    assert tools["edit_file"]("missing.py", "a", "b").startswith("Error")  # an error stays as it was

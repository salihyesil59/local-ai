import os
import sys

from local_ai.coder import verify as vf


def test_detect_command(tmp_path):
    assert vf.detect_command(tmp_path) is None
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}', encoding="utf-8")
    assert vf.detect_command(tmp_path) is None  # no test script
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}', encoding="utf-8")
    assert vf.detect_command(tmp_path) == "npm test"
    (tmp_path / "test_x.py").write_text("", encoding="utf-8")
    assert vf.detect_command(tmp_path) == "python -m pytest -x -q --tb=short"


def test_detect_other_languages(tmp_path):
    (tmp_path / "go.mod").write_text("module x", encoding="utf-8")
    assert vf.detect_command(tmp_path) == "go test ./..."
    (tmp_path / "Cargo.toml").write_text("[package]", encoding="utf-8")
    assert vf.detect_command(tmp_path) == "cargo test"


def test_project_venv_is_used(tmp_path):
    exe = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    assert vf.detect_command(tmp_path) == f'"{exe}" -m pytest -x -q --tb=short'


def test_run_pass_and_fail(tmp_path):
    ok = vf.run(f'"{sys.executable}" -c "print(42)"', tmp_path)
    assert ok.passed and ok.output == "42"
    bad = vf.run(f'"{sys.executable}" -c "import sys; print(1); sys.exit(3)"', tmp_path)
    assert not bad.passed and bad.output == "1" and not bad.timed_out


def test_run_timeout_kills(tmp_path):
    result = vf.run(f'"{sys.executable}" -c "import time; time.sleep(60)"', tmp_path, timeout=2)
    assert result.timed_out and not result.passed and "Timed out after 2 s" in result.output
    assert result.seconds < 30


def test_summarize():
    assert vf.summarize("") == "(no output)"
    assert vf.summarize("short") == "short"
    long = "A" * 5000 + "B" * 5000
    out = vf.summarize(long, limit=1000)
    assert out.startswith("A" * 500) and out.endswith("B" * 500) and "truncated" in out


def test_signature_ignores_numbers_but_not_the_error():
    a = "test_it.py:12: AssertionError\nFAILED test_it.py::test_add - assert 3 == 4\n1 failed in 0.12s"
    b = "test_it.py:15: AssertionError\nFAILED test_it.py::test_add - assert 5 == 4\n1 failed in 0.31s"
    c = "test_it.py:12: TypeError\nFAILED test_it.py::test_add - TypeError: bad\n1 failed in 0.12s"
    assert vf.signature(a) == vf.signature(b) != vf.signature(c)
    assert vf.first_error(a) == "test_it.py:12: AssertionError"
    assert vf.first_error("all quiet\nlast line") == "all quiet"


def test_a_command_of_the_model_runs_in_the_projects_environment(tmp_path):
    assert vf.in_project_env("pytest tests -q", tmp_path) == "pytest tests -q"  # no environment of its own
    scripts = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    exe = scripts / ("python.exe" if os.name == "nt" else "python")
    exe.write_text("")
    assert vf.in_project_env("pytest tests -q", tmp_path) == f'"{exe}" -m pytest tests -q'
    assert vf.in_project_env("python -m pytest -x", tmp_path) == f'"{exe}" -m pytest -x'
    assert vf.in_project_env("npm test", tmp_path) == "npm test" and vf.in_project_env("", tmp_path) == ""


def test_chosen_tests_replace_the_paths_of_the_command(tmp_path):
    (tmp_path / "tests").mkdir()
    for name in ("test_a.py", "test_b.py"):
        (tmp_path / "tests" / name).write_text("def test_x():\n    pass\n")
    python = f'"{tmp_path / "python.exe"}"'  # a path with quotes, as detect_command writes it
    command = f"{python} -m pytest tests tests/test_a.py -q -p no:cacheprovider"
    assert vf.focused(command, "tests/test_b.py::test_x", tmp_path) == (
        f"{python} -m pytest -q -p no:cacheprovider tests/test_b.py::test_x"
    )
    assert vf.focused("pytest -x", "tests/test_a.py, tests/test_b.py", tmp_path) == "pytest -x tests/test_a.py tests/test_b.py"
    for bad in ("tests/nope.py", "tests/test_a.py; echo x"):
        try:
            vf.focused("pytest", bad, tmp_path)
        except ValueError:
            continue
        raise AssertionError(bad)


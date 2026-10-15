"""local-ai bench: answer checking, the SWE-bench scoring and one chat question against a scripted model."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from local_ai.bench import cli, swe
from test_web_code import ScriptedClient

RIGHT = {
    "arithmetic": "17 × 23 = 391, 144 / 12 = 12.\n\nCEVAP: 403",
    "bat-and-ball": "CEVAP: **5 kuruş**",
    "letters": "CEVAP: 3",
    "python-output": "CEVAP: `[4, 3]`",
    "integral": "CEVAP: $\\frac{\\pi}{2}$",
    "ordering": "CEVAP: Mehmet",
    "weekday": "CEVAP: Cuma",
    "dice": "CEVAP: 1/6",
    "light-time": "CEVAP: 8 dakika",
    "complexity": "CEVAP: $O(\\log n)$",
    "hydrogen-ionization": "CEVAP: 13,6 eV",
    "nitrogen-boiling": "CEVAP: yaklaşık 77 K",
    "special-relativity": "CEVAP: 1905.",
    "chandrasekhar": "CEVAP: ≈1.4 Güneş kütlesi",
    "electron-mass": "CEVAP: 0.511 MeV",
    "carbon-14": "CEVAP: 5730 yıl",
    "speed-of-sound": "CEVAP: 343 m/s",
    "fine-structure": "CEVAP: 137",
    "planck": "CEVAP: 6.626 × 10^-34 J·s",
    "sun-surface": "CEVAP: 5778 K",
    "sevens": "CEVAP: 20",
    "clock-angle": "CEVAP: 7,5°",
    "last-digits": "CEVAP: 76",
    "birthday": "CEVAP: %51",
    "digit-sum": "CEVAP: 165",
    "factorial-zeros": "CEVAP: 249",
    "mississippi": "CEVAP: 34.650",
    "average-speed": "CEVAP: 69 km/sa",
    "stairs": "CEVAP: 89",
    "power-mod": "CEVAP: 4",
    "hippocamp": "CEVAP: 2013",
    "kepler-90": "CEVAP: 8 gezegen",
    "eris-period": "CEVAP: yaklaşık 558 yıl",
    "haumea-rotation": "CEVAP: 3,9 saat",
    "tau-mass": "CEVAP: 1776,86 MeV/c²",
    "trappist-distance": "CEVAP: 40,66 ışık yılı",
    "proxima-b-period": "CEVAP: 11,2 gün",
    "phobos-period": "CEVAP: 7 saat 39 dakika",
    "charon-discovery": "CEVAP: 1978",
}


def test_every_question_has_a_pattern_that_takes_its_right_answer_and_not_a_wrong_one():
    tasks = cli.load_tasks()
    for item in tasks["chat"] + tasks["research"]:
        assert cli.correct(RIGHT[item["id"]], item["expect"]), item["id"]
        assert not cli.correct("CEVAP: 42", item["expect"]), item["id"]
    specs = cli.code_specs(tasks)
    assert all(s["packages"] and s["url"] and s["title"] for s in specs)
    assert len({s["name"] for s in specs}) == len(specs)
    sympy, pytest_task = specs[0], next(s for s in specs if s["id"].startswith("pytest-dev"))
    assert cli.code_task(tasks, sympy, "ISSUE").startswith("Bu depo SymPy.")
    assert "testing/<dosya>.py" in cli.code_task(tasks, pytest_task, "ISSUE")
    assert cli.repo_of("pylint-dev__pylint-7080") == "pylint-dev/pylint"


def test_test_results_are_found_by_their_whole_id_and_by_their_bare_name():
    output = (
        "PASSED testing/test_skipping.py::TestXFail::test_run[a b]\n"
        "FAILED sympy/core/tests/test_basic.py::test_issue_1 - AssertionError: x\n"
        "ERROR tests/test_cli.py::test_x\n"
        "1 failed, 1 passed in 0.1s\n"
    )
    status = swe.results(output)
    assert status["testing/test_skipping.py::TestXFail::test_run[a b]"] == "PASSED"
    assert status["test_issue_1"] == "FAILED" and status["tests/test_cli.py::test_x"] == "ERROR"
    assert "1" not in status


def test_the_answer_is_the_last_answer_line_or_else_the_last_line():
    assert cli.final_answer("CEVAP: 5\nhmm, no.\nCEVAP: **7**.") == "7"
    assert cli.final_answer("Thinking...\n\n1905") == "1905"
    assert cli.final_answer("77 K\n\n> **Unverified:** Numbers not found in any tool output: 77") == "77 K"
    forgot = "Kaynama noktası **77.36 K**'dir.\n\nKaynak: Wikipedia.\n\n> **Unverified:** 77.36"
    assert cli.correct(forgot, "^77\\b") and not cli.correct("CEVAP: 42\n**77 K**", "^77\\b")
    assert not cli.correct("1905 değil, 1915", "^1905$")


def test_the_meter_counts_turns_tokens_and_cut_off_replies():
    meter = cli.Meter()
    meter("turn", tokens=100, seconds=2.5, finish="tool_calls", tool_calls=["search", "read_file"])
    meter("turn", tokens=50, seconds=1.0, finish="length", tool_calls=[])
    meter.event({"kind": "turn", "tokens": 10, "seconds": 0.5, "finish": "stop", "tool_calls": []})
    assert meter.numbers() == {
        "turns": 3,
        "tokens": 160,
        "cut_off": 1,
        "tool_calls": 2,
        "model_seconds": 4.0,
        "judge_rejected": 0,
    }


@pytest.mark.anyio
async def test_a_chat_question_is_scored_from_the_answer_line():
    client = ScriptedClient([("Hesap: 391 + 12.\n\nCEVAP: 403", [])])
    args = SimpleNamespace(max_tokens=100, temperature=0.6, top_p=0.95)
    item = {"id": "arithmetic", "question": "17 × 23 + 144 / 12?", "expect": "^403$"}
    row = await cli.run_chat_question(item, cli.load_tasks(), args, client, "m")
    assert row["ok"] and row["answer"] == "403" and row["turns"] == 1
    assert client.requests[0]["messages"][-1]["content"].endswith("CEVAP: <kısa cevap>")


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.fixture
def task(tmp_path, monkeypatch):
    """A tiny project with a bug, and a benchmark task whose hidden test finds it."""
    project, task_dir = tmp_path / "project", tmp_path / "task"
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (project / "test_calc.py").write_text("from calc import add\n\n\ndef test_old():\n    assert add(0, 0) == 0\n")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "start")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip()
    (project / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_old():\n    assert add(0, 0) == 0\n\n\ndef test_new():\n"
        "    assert add(2, 2) == 4\n"
    )
    test_patch = subprocess.run(["git", "diff"], cwd=project, capture_output=True).stdout
    git(project, "checkout", "-q", "--", ".")
    task_dir.mkdir()
    (task_dir / "test.patch").write_bytes(test_patch)
    summary = {"base_commit": commit, "FAIL_TO_PASS": ["test_new"], "PASS_TO_PASS": ["test_old"]}
    (task_dir / "task.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(swe, "python_of", lambda project: sys.executable)
    return project, task_dir


def test_scoring_runs_the_hidden_tests_on_the_changes_and_reset_drops_them(task):
    project, task_dir = task
    before = swe.evaluate(task_dir, project)
    assert not before.resolved and before.fail_to_pass == {"test_new": "FAILED"} and before.broken == []

    (project / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (project / "notes.txt").write_text("a new file of the agent")
    (project / "test_calc.py").write_text("def test_mine():\n    pass\n")  # the benchmark's version replaces it
    after = swe.evaluate(task_dir, project)
    assert after.resolved and "calc.py" in after.changed and "notes.txt" in after.changed

    swe.reset(project)
    assert not (project / "notes.txt").exists()
    assert "a - b" in (project / "calc.py").read_text(encoding="utf-8")


def test_a_run_can_be_done_in_parts_with_the_same_label(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    asked = []

    async def answer(item, tasks, args, client, model):
        asked.append(item["id"])
        return {"id": item["id"], "ok": True, "seconds": 1}

    monkeypatch.setattr(cli, "run_chat_question", answer)
    part = ["run", "--suites", "chat", "--label", "base", "--model", "m", "--temperature", "0.3"]
    assert cli.main([*part, "--count", "3"]) == 0
    assert len(asked) == 3
    assert cli.main([*part[:-2], "--temperature", "0.9"]) == 0  # the run keeps the settings it started with
    n = len(cli.load_tasks()["chat"])
    assert len(asked) == n and len(set(asked)) == n
    assert cli.main(part) == 0  # nothing left
    assert len(asked) == n
    result = json.loads(cli.find_run("base").read_text(encoding="utf-8"))
    assert len(result["suites"]["chat"]) == n and result["settings"]["temperature"] == 0.3
    assert len(list((tmp_path / "bench" / "results").iterdir())) == 1
    with pytest.raises(SystemExit):  # another model cannot go on with it
        cli.main(["run", "--suites", "chat", "--label", "base", "--model", "other"])

"""Finding the way in a large project: search, the outline of a file, the map, and what comes with a task."""

import json

import pytest

from local_ai.coder import navigate as nav
from test_coder_agent import PASSING, call, make_agent, plan_call, say

ENGINE = """\
RATE = 0.2


class Engine(Base):
    def start(self, key, fast=False):
        return compute_total(key)

    def stop(self):
        pass


def compute_total(items, rate=RATE):
    return sum(items) * (1 + rate)
"""


def big_project(root, modules=14):
    """A project with enough source files to count as large."""
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg" / "engine.py").write_text(ENGINE, encoding="utf-8")
    for n in range(modules):
        (root / "pkg" / f"mod{n}.py").write_text(f"def helper{n}(x):\n    return x + {n}\n", encoding="utf-8")
    (root / "tests" / "test_engine.py").write_text(
        "from pkg.engine import compute_total\n\n\ndef test_total():\n    assert compute_total([10]) == 12\n", "utf-8"
    )
    (root / "web.js").write_text("export function render(a) {\n  return a;\n}\nclass View {}\n", encoding="utf-8")
    (root / "data.bin").write_bytes(b"\x00\xff\x00compute_total")


def test_search_finds_definitions_and_uses(project):
    big_project(project)
    found = nav.search(project, "compute_total")
    assert found.startswith("4 matches in 2 files for 'compute_total'")  # the binary file is not searched
    assert "pkg/engine.py:6: return compute_total(key)" in found and "pkg/engine.py:12: def compute_total" in found
    assert "tests/test_engine.py:1:" in found
    assert nav.search(project, "compute_total", path="tests").startswith("2 matches in 1 files")
    assert nav.search(project, "render", glob="*.js").startswith("1 matches") and "web.js:1:" in nav.search(project, "render")
    assert nav.search(project, "engine").count("class Engine") == 1  # lower case ignores case
    assert nav.search(project, "Engine(").startswith("1 matches")  # not a regular expression: taken as text
    assert nav.search(project, "nothing_like_this").startswith("No matches")
    assert nav.search(project, "x", path="../outside").startswith("Error")
    assert "more matches not shown" in nav.search(project, "def", max_results=3)


def test_outline_gives_the_lines_of_each_definition(project):
    big_project(project)
    out = nav.outline(project, "pkg/engine.py")
    assert out.startswith("pkg/engine.py (13 lines)")
    assert "4-9  class Engine(Base)" in out and "5-6  def start(self, key, fast=False)" in out
    assert "12-13  def compute_total(items, rate=RATE)" in out
    js = nav.outline(project, "web.js")
    assert "1  function render" in js and "4  class View" in js  # other languages: by keyword, without an end line
    assert nav.outline(project, "data.bin").startswith("Error") and nav.outline(project, "nope.py").startswith("Error")


def test_the_map_shrinks_its_detail_to_fit_and_puts_the_task_first(project):
    big_project(project)
    whole = nav.repo_map(project)
    assert whole.startswith("Map of . (17 source files")
    assert "  class Engine(Base): start, stop" in whole and "  def compute_total(items, rate=RATE)" in whole
    assert whole.index("pkg/engine.py") < whole.index("tests/test_engine.py")  # tests come last
    assert "pkg/mod3.py" in nav.repo_map(project, "pkg") and "web.js" not in nav.repo_map(project, "pkg")

    names = nav.repo_map(project, budget=800)  # names without parameters
    assert len(names) <= 800 and "pkg/mod13.py (2 lines): helper13" in names and "def helper3(x)" not in names
    files = nav.repo_map(project, budget=600)  # the files alone
    assert "pkg/mod13.py (2 lines)" in files and "helper13" not in files
    tiny = nav.repo_map(project, budget=300)  # too many files even by name: the folders
    assert "pkg/ (15 files" in tiny

    focused = nav.repo_map(project, focus="compute_total gives a wrong total in the engine", budget=1500)
    assert "Closest to the task:" in focused and focused.index("pkg/engine.py") < focused.index("pkg/mod0.py")
    assert "  def compute_total(items, rate=RATE)" in focused  # the file the task names keeps its detail


def test_a_small_project_gets_no_map(project):
    (project / "one.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    assert nav.task_map(project, "anything") == ""
    big_project(project)
    assert nav.task_map(project, "fix compute_total").startswith("Map of .")


@pytest.mark.anyio
async def test_in_a_large_project_the_map_and_the_way_to_work_come_with_the_task(project):
    big_project(project)
    agent, client, events = make_agent(
        project,
        [
            say(calls=[call("search", pattern="compute_total"), call("outline", path="pkg/engine.py")]),
            say(calls=[plan_call()]),
            say("stop"),
        ],
        max_nudges=0,
    )
    await agent.run("compute_total gives a wrong total")
    first = client.requests[0]["messages"][1]["content"]
    assert "This is a large project" in first and "Do not read files from top to bottom" in first
    assert "Closest to the task:" in first and "def compute_total(items, rate=RATE)" in first
    assert {"search", "repo_map", "outline"} <= {t["function"]["name"] for t in client.requests[0]["tools"]}
    results = [m["content"] for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "pkg/engine.py:12: def compute_total" in results[0] and "class Engine(Base)" in results[1]
    assert ("log", {"text": "Large project: its map is given with the task"}) in events

    small = project.parent / "small"
    small.mkdir()
    agent, client, _ = make_agent(small, [say(calls=[call("write_file", path="x.py", content=PASSING)]), say("stop")], max_nudges=0)
    await agent.run("task")
    assert "This is a large project" not in json.dumps(client.requests[0]["messages"])

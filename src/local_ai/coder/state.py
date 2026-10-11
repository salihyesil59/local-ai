"""Plan and progress of the current coding task.

Kept on disk in <project>/.local-coder so they survive context compaction, recipe retries and new sessions:
  state.json   goal, steps, verify command, last verification
  plan.md      the same, readable
  log.jsonl    one line per plan and per verification (to compare models)

LOCAL_CODER_HOME (default ~/.local-coder) holds two small files:
  current.md   a few lines about the current step, shown to the model on every turn by Goose's Top Of Mind
               extension (GOOSE_MOIM_MESSAGE_FILE)
  active       path of the project planned last, used by `local-coder check`
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE_DIR = ".local-coder"


def home() -> Path:
    path = Path(os.environ.get("LOCAL_CODER_HOME") or "~/.local-coder").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def moim_file() -> Path:
    return home() / "current.md"


def default_root() -> Path:
    """LOCAL_CODER_PROJECT, or the working directory (Goose starts extensions in the session's folder)."""
    return Path(os.environ.get("LOCAL_CODER_PROJECT") or Path.cwd()).expanduser().resolve()


def active_root() -> Path:
    """Project for `local-coder check`: the default root if it has a plan, else the project planned last."""
    root = default_root()
    pointer = home() / "active"
    if load(root) is None and pointer.is_file():
        return Path(pointer.read_text(encoding="utf-8").strip())
    return root


def new_state(goal: str, steps: list[str], criteria: list[str], verify_command: str) -> dict:
    return {
        "goal": goal,
        "steps": [{"text": s, "done": False} for s in steps],
        "criteria": criteria,  # acceptance criteria, checked against the tests in the review
        "review": "",  # "" until the last step passes, then "open", then "done"
        "verify_command": verify_command,
        "attempts": 0,  # failed verifications of the current step
        "repeats": 0,  # consecutive failures with the same signature
        "last": None,  # {"passed": bool, "line": str, "signature": str}
        "snapshot": "",  # fingerprint of the project's files at the plan or the last passing verify
    }


def current(state: dict) -> int | None:
    """Index of the first step that is not done; None when all are done."""
    return next((i for i, step in enumerate(state["steps"]) if not step["done"]), None)


def finished(state: dict) -> bool:
    """All steps are done and the review has been answered."""
    return current(state) is None and state["review"] == "done"


def load(root: Path) -> dict | None:
    path = root / STATE_DIR / "state.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save(root: Path, state: dict) -> None:
    folder = root / STATE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gitignore").write_text("*\n", encoding="utf-8")
    (folder / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "plan.md").write_text(render(root, state) + "\n", encoding="utf-8")
    (home() / "active").write_text(str(root), encoding="utf-8")
    moim_file().write_text(reminder(root, state), encoding="utf-8")


def log_event(root: Path, **event) -> None:
    event = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    with (root / STATE_DIR / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def last_line(state: dict) -> str:
    last = state["last"]
    if last is None:
        return "not run yet"
    if last["passed"]:
        return "PASSED"
    return f"FAILED (attempt {state['attempts']} on this step): {last['line']}"


def render(root: Path, state: dict) -> str:
    index = current(state)
    lines = [
        f"Project: {root}",
        f"Goal: {state['goal']}",
        f"Verify command: {state['verify_command'] or '(detected when verify runs)'}",
        "Steps:",
    ]
    for i, step in enumerate(state["steps"]):
        mark = "x" if step["done"] else " "
        here = "   <- current" if i == index else ""
        lines.append(f"  [{mark}] {i + 1}. {step['text']}{here}")
    lines.append("Acceptance criteria:")
    lines.extend(f"  {i}. {c}" for i, c in enumerate(state["criteria"], 1))
    lines.append(f"Review: {state['review'] or 'after the last step'}")
    lines.append(f"Last verify: {last_line(state)}")
    return "\n".join(lines)


# Shown while no task is running. Without it a model may write its plan as chat text and never enter the loop.
IDLE = (
    "coder extension: no coding plan is active. If the user asks you to write, change or fix code, your first "
    "tool call must be plan (coder__plan); do not write the plan as chat text. After implementing each step, "
    "call verify (coder__verify) instead of running the tests yourself.\n"
)


def reminder(root: Path, state: dict) -> str:
    """Text for the Top Of Mind file."""
    index = current(state)
    if finished(state):
        return IDLE
    if index is None:
        return (
            f"Coding task in review (coder extension). Project: {root}\n"
            f"Goal: {state['goal']}\n"
            f"All steps are implemented. Last verify: {last_line(state)}\n"
            "Check every acceptance criterion (see status) against the tests, add the missing tests, fix what "
            "they uncover, then call verify with a note. Do not tell the user the task is done before verify "
            "reports ALL DONE.\n"
        )
    return (
        f"Coding task in progress (coder extension). Project: {root}\n"
        f"Goal: {state['goal']}\n"
        f"Current step {index + 1}/{len(state['steps'])}: {state['steps'][index]['text']}\n"
        f"Last verify: {last_line(state)}\n"
        "Implement the current step with its tests, then call verify (no throwaway debug scripts). Do not tell "
        "the user the task is done before verify reports ALL DONE.\n"
    )

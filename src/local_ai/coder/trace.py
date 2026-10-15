"""Traces: the conversations of coding runs, kept as training data for a later fine-tune.

Every run appends one JSON line to its trace file: the task, how it ended (finished, the judge's word), the tool
specs and the conversation as the model saw it, with the thinking of each reply. A compaction or a fresh start
cuts a run into segments; each segment is a conversation of its own. The bench adds a line {"resolved": ...}
after it has run the task's tests.

local-ai traces export [--out FILE] [--failed]   the good runs as one JSONL file of chat samples
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from local_ai.core.images import text_of
from local_ai.servers.common import workspace_root

if TYPE_CHECKING:
    from local_ai.coder.loop import CoderAgent, Outcome


def plain(messages: list[dict]) -> list[dict]:
    """The messages with their images left out (a trace keeps text only)."""
    return [{**m, "content": text_of(m["content"])} if isinstance(m.get("content"), list) else m for m in messages]


def record(path: Path, agent: CoderAgent, task: str, outcome: Outcome) -> None:
    """Append the run that has just ended to the trace file."""
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": agent.llm.model,
        "speed": agent.settings.speed,
        "task": task,
        "finished": outcome.finished,
        "reason": outcome.reason,
        "judge": agent.verdict,
        "tools": [t.spec() for t in agent.tools.values()],
        "segments": [plain(segment) for segment in [*agent.segments, agent.messages]],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def mark(path: Path, resolved: bool) -> None:
    """The bench's verdict on the runs above it in the file: the task's own tests passed or not."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"resolved": resolved}) + "\n")


# -- export -------------------------------------------------------------------------------------------------------


def trace_files() -> list[Path]:
    """The trace files of the UI's chats and of the bench's runs."""
    root = workspace_root()
    return sorted((root / "ui" / "runs").glob("*.trace.jsonl")) + sorted((root / "bench").rglob("*.trace.jsonl"))


def runs(path: Path) -> list[dict]:
    """The runs of a trace file, each with "resolved" set when the bench marked it."""
    found: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if "segments" in item:
            found.append(item)
        elif "resolved" in item:
            for run in found:
                run.setdefault("resolved", item["resolved"])
    return found


def good(run: dict) -> bool:
    """A run worth learning from: it finished, the judge did not reject it, and the bench's tests (if any) passed."""
    return run["finished"] and run.get("judge") is not False and run.get("resolved") is not False


def sample(segment: list[dict], tools: list[dict], meta: dict) -> dict | None:
    """One chat sample: the segment up to its last reply, the thinking as reasoning_content (the name the chat
    templates of thinking models use)."""
    messages = []
    for m in segment:
        if m["role"] == "assistant" and "reasoning" in m:
            m = {**{k: v for k, v in m.items() if k != "reasoning"}, "reasoning_content": m["reasoning"]}
        messages.append(m)
    while messages and messages[-1]["role"] != "assistant":  # a nudge or an unanswered result teaches nothing
        messages.pop()
    if not messages:
        return None
    return {"messages": messages, "tools": tools, "meta": meta}


def without_prefixes(samples: list[dict]) -> list[dict]:
    """A chat that went on is traced again with its earlier part: keep only the longest of the two."""
    keys = [[json.dumps(m, sort_keys=True, ensure_ascii=False) for m in s["messages"]] for s in samples]
    kept = []
    for i, key in enumerate(keys):
        longer = any(j != i and len(other) > len(key) and other[: len(key)] == key for j, other in enumerate(keys))
        same = any(j < i and other == key for j, other in enumerate(keys))
        if not longer and not same:
            kept.append(samples[i])
    return kept


def export(out: Path, failed: bool = False) -> tuple[int, int, int]:
    """Write the samples of the good runs (all runs with failed) to out; returns (samples, runs used, runs left)."""
    samples, used, left = [], 0, 0
    for path in trace_files():
        for run in runs(path):
            if not (failed or good(run)):
                left += 1
                continue
            used += 1
            meta = {k: run.get(k) for k in ("time", "model", "speed", "task", "reason", "judge", "resolved")}
            meta["source"] = path.name
            for segment in run["segments"]:
                if (s := sample(segment, run["tools"], meta)) is not None:
                    samples.append(s)
    samples = without_prefixes(samples)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return len(samples), used, left


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="local-ai traces", description="Training data from the traces of coding runs.")
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export", help="Write the good runs as chat samples (JSONL).")
    e.add_argument("--out", type=Path, default=None, help="Output file (default: <workspace>/traces/samples.jsonl).")
    e.add_argument("--failed", action="store_true", help="Take every run, also those that did not succeed.")
    args = p.parse_args(argv)
    out = args.out or workspace_root() / "traces" / "samples.jsonl"
    n, used, left = export(out, args.failed)
    print(f"{n} samples from {used} runs ({left} runs left out) -> {out}")
    return 0

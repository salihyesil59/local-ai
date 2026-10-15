"""Collect traces for training: the coding agent solves many small tasks against an OpenAI-compatible server (vLLM
in Colab, or LM Studio), several at once, and each run is graded by the task's own tests.

local-ai traces collect TASKS.jsonl --out DIR [--base-url URL] [--model NAME] [--concurrency 32] [--samples 2]

A task is one JSON line:
  {"id": "...", "prompt": "what to do", "files": {"solution.py": "...", "test_solution.py": "..."},
   "protected": ["test_solution.py"], "grade": "python -m pytest -q"}
The files are written to a fresh folder for every sample; after the run the protected files are written again (so
changing the tests does not help) and the grade command decides: exit code 0 = resolved. The trace of every sample
goes to DIR/traces/<id>-<k>.trace.jsonl with the bench's {"resolved": ...} line after it; `local-ai traces export
--from DIR/traces` turns the good ones into samples. Samples already graded are skipped: a stopped collection goes on.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from local_ai.coder import verify as vf
from local_ai.coder.trace import mark

GRADE = "python -m pytest -q -p no:cacheprovider"


def graded(trace: Path) -> bool:
    """True when the sample was run and graded already (also one stopped by its time limit, which has no run)."""
    return trace.is_file() and any('"resolved"' in line for line in trace.read_text(encoding="utf-8").splitlines())


def write_files(folder: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


async def solve(task: dict, k: int, args, client) -> dict:
    """One sample of one task: a fresh folder, a run of the coding agent with a trace, then the grade."""
    from local_ai.coder.loop import CoderAgent, Settings
    from local_ai.core.llm import LLM

    name = f"{task['id']}-{k}"
    folder = (args.work or args.out / "work") / name
    trace = args.out / "traces" / f"{name}.trace.jsonl"
    shutil.rmtree(folder, ignore_errors=True)
    trace.unlink(missing_ok=True)  # a sample stopped before its grade starts again
    write_files(folder, task["files"])
    llm = LLM(client, args.model, max_tokens=args.max_tokens, temperature=args.temperature)
    settings = Settings(max_turns=args.max_turns, context_chars=args.context_chars, judge_rounds=args.judge_rounds)
    turns = 0

    def emit(kind, **data):
        nonlocal turns
        turns += kind == "turn"

    agent = CoderAgent(llm, folder, settings, emit=emit, trace=trace)
    started, reason = time.monotonic(), ""
    try:
        reason = (await asyncio.wait_for(agent.run(task["prompt"]), args.minutes * 60)).reason
    except asyncio.TimeoutError:
        reason = f"stopped after {args.minutes} minutes"
    except Exception as e:  # a broken sample must not stop the others; it is graded as failed
        reason = f"error: {type(e).__name__}: {e}"
    protected = {p: task["files"][p] for p in task.get("protected", [])}
    write_files(folder, protected)
    result = await asyncio.to_thread(vf.run, task.get("grade", GRADE), folder, args.grade_timeout)
    mark(trace, result.passed)  # a run stopped by the time limit leaves only this line
    shutil.rmtree(folder, ignore_errors=True)
    return {"id": task["id"], "sample": k, "reason": reason, "resolved": result.passed, "turns": turns,
            "seconds": round(time.monotonic() - started, 1)}


async def collect(args, client=None) -> int:
    """Run every task's samples that have no grade yet; client: an openai.AsyncOpenAI (made from args when None)."""
    from openai import AsyncOpenAI

    tasks = [json.loads(line) for line in args.tasks.read_text(encoding="utf-8").splitlines() if line.strip()]
    todo = [(t, k) for t in tasks for k in range(args.samples)
            if not graded(args.out / "traces" / f"{t['id']}-{k}.trace.jsonl")]
    print(f"{len(tasks)} tasks x {args.samples} samples: {len(todo)} to run", flush=True)
    client = client or AsyncOpenAI(base_url=args.base_url, api_key=args.api_key, timeout=None)
    if not args.model:
        args.model = (await client.models.list()).data[0].id
    gate = asyncio.Semaphore(args.concurrency)
    deadline = time.monotonic() + args.hours * 3600 if args.hours else None
    results = args.out / "results.jsonl"
    results.parent.mkdir(parents=True, exist_ok=True)
    done = resolved = 0

    async def one(task: dict, k: int) -> None:
        nonlocal done, resolved
        async with gate:
            if deadline and time.monotonic() > deadline:  # the time budget is spent: the rest waits for a next time
                return
            row = await solve(task, k, args, client)
        done += 1
        resolved += row["resolved"]
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{done}/{len(todo)}] {row['id']}-{k}: {'resolved' if row['resolved'] else 'failed'} "
              f"({row['turns']} turns, {row['seconds']:.0f} s; {resolved} resolved so far)", flush=True)

    await asyncio.gather(*(one(t, k) for t, k in todo))
    print(f"{resolved}/{done} resolved -> {args.out / 'traces'}")
    return 0


def add_parser(sub) -> None:
    c = sub.add_parser("collect", help="Solve tasks with the coding agent against a server and keep graded traces.")
    c.add_argument("tasks", type=Path, help="JSONL file of tasks (see the module's help).")
    c.add_argument("--out", type=Path, required=True, help="Folder for traces, results and the work folders.")
    c.add_argument("--work", type=Path, default=None,
                   help="Folder for the project folders of the runs (default: OUT/work); a fast local disk.")
    c.add_argument("--base-url", default="http://127.0.0.1:1234/v1", help="OpenAI-compatible server (vLLM, LM Studio).")
    c.add_argument("--api-key", default="local", help="Key for the server (vLLM and LM Studio accept any).")
    c.add_argument("--model", default="", help="Model name on the server (default: the first it lists).")
    c.add_argument("--concurrency", type=int, default=8, help="Tasks worked on at the same time.")
    c.add_argument("--samples", type=int, default=2, help="Runs per task (the best are picked later).")
    c.add_argument("--temperature", type=float, default=0.8)
    c.add_argument("--max-tokens", type=int, default=8192, help="Tokens per model reply, thinking included.")
    c.add_argument("--max-turns", type=int, default=30)
    c.add_argument("--context-chars", type=int, default=24_000,
                   help="Compact above this: keeps every segment short enough for training.")
    c.add_argument("--judge-rounds", type=int, default=1)
    c.add_argument("--minutes", type=float, default=20, help="Time limit of one sample.")
    c.add_argument("--grade-timeout", type=float, default=120)
    c.add_argument("--hours", type=float, default=0, help="Start no new sample after this many hours (0 = no limit).")

"""`local-ai bench`: a fixed set of tasks to measure the assistant with, so a change (a prompt, a tool, a model,
a setting) is judged by numbers instead of by one run.

  local-ai bench setup                 download and prepare the coding tasks (online, once)
  local-ai bench run [options]         run the tasks, print a table and save the results
  local-ai bench show                  compare the saved runs

A run can be done in parts: run again with the same --label and it goes on where it stopped (tasks that already
have a result are skipped), e.g. `local-ai bench run --label baseline --count 2` whenever there is time.

Coding tasks are SWE-bench Lite issues of SymPy, scored with the benchmark's own hidden tests. Chat and research
questions have one short right answer, checked with a pattern. Everything lives in <workspace>/bench.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from local_ai.bench import swe
from local_ai.servers.common import workspace_root

SUITES = ("code", "chat", "research")
SETTINGS = ("speed", "judge_rounds", "max_turns", "minutes", "max_tokens", "temperature", "top_p", "research")
CHAT_THINKING_CHARS = 16_000  # as in the chat mode of the web UI


def bench_dir() -> Path:
    return workspace_root() / "bench"


def load_tasks() -> dict:
    return json.loads(resources.files("local_ai.bench").joinpath("tasks.json").read_text(encoding="utf-8"))


def short(instance_id: str) -> str:
    return instance_id.split("__")[-1]  # sympy__sympy-24102 -> sympy-24102


def repo_of(instance_id: str) -> str:
    owner, rest = instance_id.split("__", 1)  # pytest-dev__pytest-7432 -> pytest-dev/pytest
    return f"{owner}/{rest.rsplit('-', 1)[0]}"


def code_specs(tasks: dict, only: list[str] | None = None) -> list[dict]:
    """The coding tasks with the settings of their repository (a task's own settings win)."""
    specs = [{**tasks["repos"][repo_of(t["id"])], **t, "name": short(t["id"])} for t in tasks["code"]]
    return [s for s in specs if not only or s["name"] in only or s["id"] in only]


def code_task(tasks: dict, spec: dict, issue: str) -> str:
    """What the agent is asked: the issue, with how to read the repository and which tests to give verify."""
    return tasks["code_prompt"].format(name=spec["title"], reading=spec["reading"], tests=spec["tests"], issue=issue)


# -- scoring answers ----------------------------------------------------------------------------------------------


def clean(answer: str) -> str:
    answer = re.sub(r"[*$]|\\\(|\\\)", "", answer).strip().rstrip(".")
    answer = re.sub(r"^(?:[≈~]|yaklaşık|approximately|about)\s*", "", answer, flags=re.I)
    return answer.strip()


def answers(text: str) -> list[str]:
    """The short answer of a reply, without formatting: what follows the last "CEVAP:". A reply that forgot that
    line gives its bold parts (where a model puts its result) and its last line instead."""
    found = re.findall(r"(?:CEVAP|ANSWER)\s*[:：]\s*(.+)", text, flags=re.I)
    if found:
        return [clean(found[-1])]
    # the research agent may add a note on numbers no tool backed; it is not the answer
    lines = [line for line in text.strip().splitlines() if line.strip() and "Unverified:" not in line]
    bold = re.findall(r"\*\*(.+?)\*\*", "\n".join(lines))
    return [clean(a) for a in [*bold, *lines[-1:]]]


def final_answer(text: str) -> str:
    found = answers(text)
    return found[0] if found else ""


def correct(text: str, pattern: str) -> bool:
    return any(re.search(pattern, a, flags=re.I) for a in answers(text))


# -- running ------------------------------------------------------------------------------------------------------


@dataclass
class Meter:
    """Counts what the replies of one task cost, from the events of an agent."""

    turns: int = 0
    tokens: int = 0
    cut_off: int = 0
    tool_calls: int = 0
    model_seconds: float = 0.0
    judge_rejected: int = 0  # times the judge sent a finished coding task back
    restarts: int = 0  # fresh starts of a coding task that kept getting stuck
    tools: dict[str, int] = field(default_factory=dict)

    def __call__(self, kind: str, **data) -> None:
        if kind == "turn":
            self.turns += 1
            self.tokens += data.get("tokens") or 0
            self.model_seconds += data.get("seconds") or 0
            self.cut_off += data.get("finish") == "length"
            for name in data.get("tool_calls") or []:
                self.tools[name] = self.tools.get(name, 0) + 1
        elif kind == "tool_call":
            self.tool_calls += 1
        elif kind == "judge" and not data.get("solved"):
            self.judge_rejected += 1
        elif kind == "restart":
            self.restarts += 1

    def event(self, event: dict) -> None:  # the research agent passes one dict per event
        self(event["kind"], **{k: v for k, v in event.items() if k != "kind"})

    def numbers(self) -> dict:
        return {
            "turns": self.turns,
            "tokens": self.tokens,
            "cut_off": self.cut_off,
            "tool_calls": self.tool_calls or sum(self.tools.values()),
            "model_seconds": round(self.model_seconds, 1),
            "judge_rejected": self.judge_rejected,
            "restarts": self.restarts,
        }


def say(text: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding), flush=True)


async def run_code_task(spec: dict, tasks: dict, args, client, model: str, out: Path) -> dict:
    from local_ai.coder import state as coder_state
    from local_ai.coder.loop import CoderAgent, Settings
    from local_ai.core.llm import LLM

    task_dir, project = bench_dir() / "tasks" / spec["name"], bench_dir() / "projects" / spec["name"]
    if not (task_dir / "ready").exists():
        return {"id": spec["name"], "ok": False, "error": "not set up: run `local-ai bench setup`"}
    swe.reset(project)
    os.environ["LOCAL_CODER_PROJECT"] = str(project)
    coder_state.moim_file().write_text(coder_state.IDLE, encoding="utf-8")
    issue = (task_dir / "issue.md").read_text(encoding="utf-8")
    meter = Meter()
    llm = LLM(client, model, max_tokens=args.max_tokens, temperature=args.temperature, top_p=args.top_p)
    limits = Settings(speed=args.speed, max_turns=args.max_turns, judge_rounds=getattr(args, "judge_rounds", 0))
    agent = CoderAgent(llm, project, limits, emit=meter)
    started, reason = time.monotonic(), ""
    try:
        outcome = await asyncio.wait_for(agent.run(code_task(tasks, spec, issue)), args.minutes * 60)
        reason = outcome.reason
    except asyncio.TimeoutError:
        reason = f"stopped after {args.minutes} minutes"
    seconds = time.monotonic() - started
    score = await asyncio.to_thread(swe.evaluate, task_dir, project)
    (out / f"{spec['name']}.messages.json").write_text(json.dumps(agent.messages, ensure_ascii=False), encoding="utf-8")
    return {
        "id": spec["name"],
        "ok": score.resolved,
        "seconds": round(seconds),
        "reason": reason,
        "fail_to_pass": score.fail_to_pass,
        "broken": score.broken,
        "changed": score.changed,
        **meter.numbers(),
    }


async def run_chat_question(item: dict, tasks: dict, args, client, model: str) -> dict:
    from local_ai.chat import ChatAgent
    from local_ai.core.llm import LLM

    meter = Meter()
    llm = LLM(
        client,
        model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        max_thinking_chars=CHAT_THINKING_CHARS,
    )
    started = time.monotonic()
    answer = await ChatAgent(llm, emit=meter).reply(item["question"] + tasks["answer_rule"])
    return {
        "id": item["id"],
        "ok": correct(answer, item["expect"]),
        "seconds": round(time.monotonic() - started),
        "answer": final_answer(answer)[:200],
        "text": answer[-2000:],
        **meter.numbers(),
    }


async def run_research_question(item: dict, tasks: dict, args, client, model: str, bridge) -> dict:
    from local_ai.research.config import AgentSettings
    from local_ai.research.loop import ResearchAgent

    meter = Meter()
    settings = AgentSettings(model=model, temperature=args.temperature, top_p=args.top_p, max_tokens=args.max_tokens)
    settings.critic = settings.gap_check = settings.learn = False  # the fast research of the web UI
    agent = ResearchAgent(client, bridge, settings, on_event=meter.event)
    question = item["question"] + tasks["answer_rule"]
    started = time.monotonic()
    answer = await (agent.quick(question) if args.research == "quick" else agent.run(question, project=None))
    return {
        "id": item["id"],
        "ok": correct(answer, item["expect"]),
        "seconds": round(time.monotonic() - started),
        "answer": final_answer(answer)[:200],
        "text": answer[-2000:],
        **meter.numbers(),
    }


async def run_all(args) -> int:
    from openai import AsyncOpenAI

    from local_ai.core.llm import DEFAULT_BASE_URL, pick_model

    tasks = load_tasks()
    suites = args.suites.split(",") if args.suites else list(SUITES)
    only = args.only.split(",") if args.only else None
    client = AsyncOpenAI(
        base_url=args.base_url or os.environ.get("LOCAL_AI_BASE_URL", DEFAULT_BASE_URL),
        api_key=os.environ.get("LOCAL_AI_API_KEY", "lm-studio"),
    )
    model = args.model or await pick_model(client)
    if not model:
        raise SystemExit("No model loaded in LM Studio. Load one or pass --model.")
    earlier = find_run(args.label) if args.label else None
    if earlier:  # the same label: go on with that run, with its settings, so its parts stay comparable
        out = earlier.parent
        result = json.loads(earlier.read_text(encoding="utf-8"))
        if result["model"] != model:
            raise SystemExit(
                f"The run '{args.label}' was made with {result['model']}, not {model}. Load that model or use "
                "another --label."
            )
        for key, value in result["settings"].items():
            setattr(args, key, value)
        done = sum(len(rows) for rows in result["suites"].values())
        say(f"Going on with the run '{args.label}' ({done} tasks have a result).")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = bench_dir() / "results" / (f"{stamp}-{args.label}" if args.label else stamp)
        out.mkdir(parents=True, exist_ok=True)
        settings = {k: getattr(args, k) for k in SETTINGS}
        result = {"label": args.label or "", "model": model, "started": stamp, "settings": settings, "suites": {}}
    say(f"Model: {model}\nResults: {out}")
    left = [args.count]  # tasks still to run in this part (None: all)

    def save() -> None:
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    async def suite(name: str, items: list, work) -> None:
        rows = result["suites"].setdefault(name, [])
        for item in items:
            label = item.get("name") or item["id"]
            if any(r["id"] == label for r in rows):
                if not args.again:
                    continue
                rows[:] = [r for r in rows if r["id"] != label]
            if left[0] is not None:
                if left[0] <= 0:
                    return
                left[0] -= 1
            say(f"[{name}] {label} ...")
            try:
                row = await work(item)
            except Exception as error:  # one broken task must not end the whole run; it counts as failed
                row = {"id": label, "ok": False, "error": f"{type(error).__name__}: {error}"[:500]}
            rows.append(row)
            save()
            detail = row.get("answer") or row.get("reason") or row.get("error") or ""
            say(
                f"    {'OK  ' if row['ok'] else 'FAIL'} {row.get('seconds', 0)} s, {row.get('turns', 0)} turns  {detail}"
            )

    def pick(items: list) -> list:
        return [i for i in items if not only or i["id"] in only]

    if "chat" in suites:
        await suite("chat", pick(tasks["chat"]), lambda item: run_chat_question(item, tasks, args, client, model))
    if "research" in suites:
        from local_ai.core.mcp_bridge import MCPBridge, load_mcp_config

        servers = load_mcp_config(args.mcp_config, args.servers.split(",") if args.servers else None)
        async with MCPBridge(servers) as bridge:
            await suite(
                "research",
                pick(tasks["research"]),
                lambda item: run_research_question(item, tasks, args, client, model, bridge),
            )
    if "code" in suites:
        await suite("code", code_specs(tasks, only), lambda spec: run_code_task(spec, tasks, args, client, model, out))
    result["updated"] = time.strftime("%Y%m%d-%H%M%S")
    save()
    say("\n" + table([result]))
    missing = {name: total - len(result["suites"].get(name, [])) for name, total in totals().items()}
    missing = {name: n for name, n in missing.items() if n > 0}
    if missing and args.label:
        what = ", ".join(f"{n} {name}" for name, n in missing.items())
        say(f"\nNot run yet: {what}. Go on with: local-ai bench run --label {args.label}")
    return 0


def find_run(label: str) -> Path | None:
    """The result file of the newest run with this label."""
    for path in sorted((bench_dir() / "results").glob("*/result.json"), reverse=True):
        if json.loads(path.read_text(encoding="utf-8")).get("label") == label:
            return path
    return None


def totals() -> dict[str, int]:
    tasks = load_tasks()
    return {name: len(tasks[name]) for name in SUITES}


# -- reports ------------------------------------------------------------------------------------------------------


def summary(result: dict) -> dict[str, str]:
    cells = {"run": f"{result['started']} {result.get('label', '')}".strip(), "model": result["model"][:28]}
    for name, total in totals().items():
        rows = result["suites"].get(name)
        if not rows:
            cells[name] = "-"
            continue
        solved = sum(r["ok"] for r in rows)
        minutes = sum(r.get("seconds", 0) for r in rows) / 60
        part = f", {len(rows)} of {total} run" if len(rows) < total else ""
        cells[name] = f"{solved}/{len(rows)} ({minutes:.0f} min{part})"
    return cells


def table(results: list[dict]) -> str:
    rows = [summary(r) for r in results]
    columns = ["run", "model", *SUITES]
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in columns}
    lines = ["  ".join(c.ljust(widths[c]) for c in columns), "  ".join("-" * widths[c] for c in columns)]
    lines += ["  ".join(r[c].ljust(widths[c]) for c in columns) for r in rows]
    if len(results) == 1:  # one run: every task too
        for name, items in results[0]["suites"].items():
            lines.append(f"\n{name}:")
            for r in items:
                extra = r.get("answer") or r.get("reason") or r.get("error") or ""
                lines.append(
                    f"  {'OK  ' if r['ok'] else 'FAIL'} {r['id']:<22} {r.get('seconds', 0):>5} s  {extra[:70]}"
                )
    return "\n".join(lines)


def rescore(args) -> int:
    """Score the saved chat and research answers of a run again, with the patterns and rules of now."""
    found = find_run(args.label)
    if not found:
        say(f"No run with the label '{args.label}'.")
        return 1
    result = json.loads(found.read_text(encoding="utf-8"))
    tasks = load_tasks()
    changed = 0
    for name in ("chat", "research"):
        expect = {item["id"]: item["expect"] for item in tasks[name]}
        for row in result["suites"].get(name, []):
            if "text" not in row or row["id"] not in expect:
                continue  # saved before answers were kept whole, or a question that was dropped
            ok = correct(row["text"], expect[row["id"]])
            if ok != row["ok"]:
                changed += 1
                say(f"{name}/{row['id']}: {'FAIL -> OK' if ok else 'OK -> FAIL'}")
            row["ok"], row["answer"] = ok, final_answer(row["text"])[:200]
    found.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"{changed} answers scored differently.\n\n" + table([result]))
    return 0


def show(args) -> int:
    files = sorted((bench_dir() / "results").glob("*/result.json"))
    if not files:
        say("No saved runs yet: `local-ai bench run`.")
        return 1
    if args.label:  # one run, with every task
        found = find_run(args.label)
        if not found:
            say(f"No run with the label '{args.label}'.")
            return 1
        files = [found]
    results = [json.loads(f.read_text(encoding="utf-8")) for f in files[-args.last :]]
    say(table(results))
    return 0


def setup(args) -> int:
    tasks = load_tasks()
    failed = 0
    for spec in code_specs(tasks, args.only.split(",") if args.only else None):
        task_dir, project = bench_dir() / "tasks" / spec["name"], bench_dir() / "projects" / spec["name"]
        if (task_dir / "ready").exists() and not args.again:
            say(f"{spec['name']}: ready")
            continue
        say(f"{spec['name']}: preparing ...")
        try:
            detail = swe.prepare(spec, task_dir, project, bench_dir() / "swe_lite.json")
        except (swe.SetupError, OSError) as error:
            failed += 1
            say(f"    FAILED: {error}")
            continue
        (task_dir / "ready").write_text(detail, encoding="utf-8")
        say(f"    ready: {detail}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="local-ai bench", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup", help="download and prepare the coding tasks")
    s.add_argument("--only", help="comma-separated task names, e.g. sympy-24102")
    s.add_argument("--again", action="store_true", help="prepare tasks that are already ready again")
    r = sub.add_parser("run", help="run the tasks and save the results")
    r.add_argument("--suites", help=f"comma-separated subset of {','.join(SUITES)} (default: all)")
    r.add_argument("--only", help="comma-separated task ids")
    r.add_argument("--label", help="a name for this run; the same label again goes on with that run")
    r.add_argument("--count", type=int, help="run at most this many tasks now (a part of the run)")
    r.add_argument("--again", action="store_true", help="run tasks again that already have a result")
    r.add_argument("--model", help="default: the first loaded model")
    r.add_argument("--base-url")
    r.add_argument("--speed", choices=("normal", "fast", "quick"), default="normal", help="of the coding agent")
    r.add_argument("--judge-rounds", type=int, default=2, help="times the judge may send a coding task back (0: off)")
    r.add_argument("--max-turns", type=int, default=80, help="per coding task")
    r.add_argument("--minutes", type=float, default=40, help="time limit per coding task")
    r.add_argument("--max-tokens", type=int, default=8192)
    r.add_argument("--temperature", type=float, default=0.6)
    r.add_argument("--top-p", type=float, default=0.95)
    r.add_argument("--research", choices=("quick", "full"), default="quick", help="research mode for its questions")
    r.add_argument("--mcp-config", help="for the research questions (default as `local-ai`)")
    r.add_argument("--servers", help="comma-separated MCP servers for the research questions (default: all)")
    w = sub.add_parser("show", help="compare the saved runs")
    w.add_argument("--last", type=int, default=10, help="how many of the newest runs")
    w.add_argument("--label", help="show one run with every task")
    c = sub.add_parser("rescore", help="score the saved chat and research answers of a run again")
    c.add_argument("--label", required=True)
    args = p.parse_args(argv)
    if args.command == "setup":
        return setup(args)
    if args.command == "show":
        return show(args)
    if args.command == "rescore":
        return rescore(args)
    return asyncio.run(run_all(args))

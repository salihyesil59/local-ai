"""`local-coder run "task"`: run the coding agent against a model served by LM Studio."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from local_ai.coder import state as st
from local_ai.coder.agent.llm import LLM
from local_ai.coder.agent.loop import CoderAgent, Settings

DEFAULT_BASE_URL = "http://localhost:1234/v1"


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("task", nargs="?", help="what to build, change or fix (or use --task-file)")
    p.add_argument("--task-file", type=Path, help="read the task from a file")
    p.add_argument("--project", type=Path, default=Path.cwd(), help="project folder (default: the current one)")
    p.add_argument("--base-url", default=os.environ.get("LOCAL_CODER_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--model", default=os.environ.get("LOCAL_CODER_MODEL"), help="default: the first loaded model")
    p.add_argument("--max-tokens", type=int, default=8192, help="tokens per model reply, thinking included")
    p.add_argument(
        "--max-thinking-chars",
        type=int,
        default=8_000,
        help="stop a reply that only thinks after this many characters",
    )
    p.add_argument("--max-turns", type=int, default=80, help="model replies in one run")
    p.add_argument("--temperature", type=float, default=0.6)
    p.add_argument("--presence-penalty", type=float, help="off by default; 0.5 can reduce repetition")
    p.add_argument("-v", "--verbose", action="store_true", help="show tool results")


class Printer:
    """Prints what happens, one line per event."""

    def __init__(self, verbose: bool):
        self.verbose = verbose

    def __call__(self, kind: str, **d) -> None:
        if kind == "turn":
            tokens = f", {d['tokens']} tokens" if d["tokens"] else ""
            tools = " -> " + ", ".join(d["tool_calls"]) if d["tool_calls"] else ""
            cut = "  [CUT OFF]" if d["finish"] == "length" else ""
            self.line(f"[turn {d['turn']}] {d['seconds']:.0f} s{tokens}{tools}{cut}")
        elif kind == "tool_call" and self.verbose:
            try:
                args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in json.loads(d["arguments"] or "{}").items())
            except (json.JSONDecodeError, AttributeError):
                args = d["arguments"][:100]
            self.line(f"  {d['name']}({args})")
        elif kind == "tool_result" and self.verbose:
            self.line("  = " + d["output"].strip().splitlines()[0][:150] if d["output"].strip() else "  = (empty)")
        elif kind == "auto_verify":
            self.line("  [program ran verify] " + d["output"].strip().splitlines()[0][:150])
        elif kind == "compact":
            self.line(f"  [context compacted, {d['dropped']} messages dropped]")

    def line(self, text: str) -> None:
        print(text.encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8"), flush=True)


async def pick_model(client) -> str:
    ids = [m.id for m in (await client.models.list()).data if "embed" not in m.id.lower()]
    if not ids:
        raise SystemExit("No model loaded in LM Studio. Load one or pass --model.")
    return ids[0]


async def run(args: argparse.Namespace) -> int:
    from openai import AsyncOpenAI

    task = args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
    if not task:
        raise SystemExit("Give the task as an argument or with --task-file.")
    root = args.project.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_CODER_PROJECT"] = str(root)

    client = AsyncOpenAI(base_url=args.base_url, api_key=os.environ.get("LOCAL_CODER_API_KEY", "lm-studio"))
    model = args.model or await pick_model(client)
    print(f"Model: {model} @ {args.base_url}\nProject: {root}", flush=True)
    st.moim_file().write_text(st.IDLE, encoding="utf-8")

    llm = LLM(
        client,
        model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        presence_penalty=args.presence_penalty,
        max_thinking_chars=args.max_thinking_chars,
    )
    agent = CoderAgent(llm, root, Settings(max_turns=args.max_turns), emit=Printer(args.verbose))
    outcome = await agent.run(task)
    print(f"\n{'FINISHED' if outcome.finished else 'NOT FINISHED'}: {outcome.reason}")
    if outcome.summary:
        print(outcome.summary)
    return 0 if outcome.finished else 1


def main(args: argparse.Namespace) -> None:
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit(130)

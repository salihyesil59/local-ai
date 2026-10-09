"""CLI.

local-ai "your question" [options]     run a research task
local-ai --quick "your question"       fast answer: one tool loop, no report
local-ai ui [--port 8765]               local web interface
local-ai export <project> [--format html|md|bib|tex|docx|pdf]
local-ai data ...                       download/update ZIM and arXiv data (online)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from openai import AsyncOpenAI
from rich.console import Console
from rich.markdown import Markdown

from .config import AgentSettings, load_mcp_config
from .loop import ResearchAgent
from .mcp_bridge import MCPBridge


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="local-ai",
        description="Research agent: LM Studio model + MCP tools (library, compute, notebook).",
    )
    p.add_argument("question", nargs="?", help="Research question (any language).")
    p.add_argument("--project", help="Notebook project name (default: derived from the question).")
    p.add_argument("--resume", action="store_true", help="Continue an existing --project from its saved notes.")
    p.add_argument("--out", type=Path, help="Also write the final report to this file.")
    p.add_argument("--mcp-config", help="Path to mcp.json (default: LOCAL_AI_MCP_CONFIG, else ~/.lmstudio/mcp.json).")
    p.add_argument("--servers", help="Comma-separated subset of MCP servers to start (default: all).")
    p.add_argument("--base-url", help="OpenAI-compatible endpoint (default: http://localhost:1234/v1).")
    p.add_argument("--model", help="Model id as shown by LM Studio (default: first loaded LLM).")
    p.add_argument("--max-steps", type=int, help="Tool-calling steps per sub-question (default: 12).")
    p.add_argument("--max-tool-chars", type=int, help="Characters of tool output kept in context (default: 6000).")
    p.add_argument("--temperature", type=float)
    p.add_argument("--max-tokens", type=int, help="Tokens per model reply, thinking included (default: 8192).")
    p.add_argument("--no-critic", action="store_true", help="Skip the reviewer pass on the draft.")
    p.add_argument("--no-gap-check", action="store_true", help="Don't add sub-questions for gaps found after research.")
    p.add_argument("--no-learn", action="store_true", help="Don't save facts/lessons to long-term memory.")
    p.add_argument("--fast", action="store_true", help="Same as --no-critic --no-gap-check --no-learn.")
    p.add_argument(
        "--quick", action="store_true", help="Fast answer: one tool-calling loop on the question, no plan or report."
    )
    p.add_argument("--parallel", type=int, help="Sub-questions researched at the same time (default: 1).")
    p.add_argument("-v", "--verbose", action="store_true", help="Show every tool call and finding.")
    args = p.parse_args(argv)
    if not args.question and not args.resume:
        p.error("a question is required (or use --resume --project NAME)")
    if args.quick and (args.resume or not args.question):
        p.error("--quick needs a question and cannot be combined with --resume")
    return args


async def pick_model(client: AsyncOpenAI) -> str:
    models = await client.models.list()
    ids = [m.id for m in models.data if "embed" not in m.id.lower()]
    if not ids:
        raise SystemExit("No model loaded in LM Studio. Load one or pass --model.")
    return ids[0]


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    settings = AgentSettings()
    for key in ("base_url", "model", "max_steps", "max_tool_chars", "temperature", "max_tokens", "parallel"):
        value = getattr(args, key)
        if value is not None:
            setattr(settings, key, value)
    settings.critic = not (args.no_critic or args.fast)
    settings.gap_check = not (args.no_gap_check or args.fast)
    settings.learn = not (args.no_learn or args.fast)

    client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
    if not settings.model:
        settings.model = await pick_model(client)
    console.print(f"Model: {settings.model} @ {settings.base_url}", style="dim")

    only = [s.strip() for s in args.servers.split(",")] if args.servers else None
    servers = load_mcp_config(args.mcp_config, only)
    console.print(f"MCP servers: {', '.join(s.name for s in servers)}", style="dim")

    async with MCPBridge(servers) as bridge:
        console.print(f"Tools: {len(bridge.tools)}", style="dim")
        agent = ResearchAgent(client, bridge, settings, console=console, verbose=args.verbose)
        if args.quick:
            report = await agent.quick(args.question)
        else:
            report = await agent.run(args.question, project=args.project, resume=args.resume)

    console.rule("Answer" if args.quick else "Report")
    console.print(Markdown(report))
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        console.print(f"Saved to {args.out}", style="green")
    return 0


def export_main(argv: list[str]) -> int:
    from local_ai.servers.common import slugify, workspace_root
    from local_ai.servers.export import FORMATS, ExportError, export

    p = argparse.ArgumentParser(prog="local-ai export", description="Export a project's report.")
    p.add_argument("project", help="project name (folder in the research workspace)")
    p.add_argument("--format", "-f", action="append", choices=FORMATS, help="repeatable; default html")
    args = p.parse_args(argv)
    project_dir = workspace_root() / slugify(args.project)
    status = 0
    for fmt in args.format or ["html"]:
        try:
            print(export(project_dir, fmt))
        except ExportError as e:
            print(f"{fmt}: {e}", file=sys.stderr)
            status = 1
    return status


def cli() -> None:
    argv = sys.argv[1:]
    try:
        if argv and argv[0] == "ui":
            from .web.app import main as ui_main

            sys.exit(ui_main(argv[1:]))
        if argv and argv[0] == "export":
            sys.exit(export_main(argv[1:]))
        if argv and argv[0] == "data":
            from .data import main as data_main

            sys.exit(data_main(argv[1:]))
        sys.exit(asyncio.run(main(argv)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    cli()

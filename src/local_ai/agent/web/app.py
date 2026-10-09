"""Local web interface for the research agent.

Runs on 127.0.0.1 only. Starts the MCP servers once, runs one research task at a time
(the compute kernel is shared) and streams the agent's events to the browser over SSE.

  local-ai ui [--port 8765] [--no-browser] [--mcp-config PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sqlite3
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from local_ai.servers.common import slugify, workspace_root
from local_ai.servers.export import FORMATS, ExportError, export, render_body, render_markdown

from ..config import AgentSettings, ServerConfig, load_mcp_config
from ..loop import ResearchAgent
from ..mcp_bridge import MCPBridge

STATIC = Path(__file__).parent / "static"


@dataclass
class Run:
    id: str
    question: str
    project: str
    status: str = "queued"  # queued | running | done | failed | cancelled
    events: list[dict] = field(default_factory=list)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None

    def push(self, event: dict) -> None:
        if event["kind"] not in ("token", "stream_end"):  # live output is not replayed to late listeners
            self.events.append(event)
        for q in self.listeners:
            q.put_nowait(event)

    def summary(self) -> dict:
        return {"id": self.id, "question": self.question, "project": self.project, "status": self.status}


def resolve_workspace(servers: list[ServerConfig]) -> Path:
    """Use the notebook server's workspace so the UI reads the same files it writes."""
    for s in servers:
        if s.name == "notebook" and s.env.get("LOCAL_AI_WORKSPACE"):
            return Path(s.env["LOCAL_AI_WORKSPACE"]).expanduser().resolve()
    return workspace_root()


def parse_memory_lines(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        m = re.match(r"\[m(\d+)\] \((\w+)\) (.*?)(?:  — sources: (.*))?$", line)
        if m:
            items.append({"id": int(m[1]), "kind": m[2], "text": m[3], "sources": m[4] or ""})
    return items


def create_app(servers: list[ServerConfig], settings: AgentSettings, client=None) -> Starlette:
    workspace = resolve_workspace(servers)
    os.environ.setdefault("LOCAL_AI_WORKSPACE", str(workspace))
    state: dict = {"bridge": None, "client": client, "runs": {}, "lock": asyncio.Lock(), "error": None}

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if state["client"] is None:
            from openai import AsyncOpenAI

            state["client"] = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
        try:
            async with MCPBridge(servers) as bridge:
                state["bridge"] = bridge
                yield
        except Exception as e:  # keep the UI up to show the problem
            state["error"] = f"Could not start MCP servers: {e}"
            yield

    # -- helpers ------------------------------------------------------------------

    def project_dir(name: str) -> Path:
        path = (workspace / slugify(name)).resolve()
        if not path.is_relative_to(workspace) or not path.is_dir():
            raise FileNotFoundError(name)
        return path

    def decorate(event: dict) -> dict:
        """Add rendered HTML to events whose text is Markdown."""
        if event["kind"] in ("findings", "answer"):
            event = {**event, "html": render_markdown(event["text"])}
        elif event["kind"] == "report":
            try:
                pdir = project_dir(event["project"])
                sources = (
                    json.loads((pdir / "sources.json").read_text(encoding="utf-8"))
                    if (pdir / "sources.json").exists()
                    else []
                )
                body, refs = render_body(event["text"], sources, pdir)
                event = {**event, "html": body + refs}
            except FileNotFoundError:
                event = {**event, "html": render_markdown(event["text"])}
        return event

    async def execute(run: Run, options: dict) -> None:
        async with state["lock"]:
            run.status = "running"
            run.push({"kind": "status", "status": "running"})
            try:
                if not settings.model:
                    models = await state["client"].models.list()
                    ids = [m.id for m in models.data if "embed" not in m.id.lower()]
                    if not ids:
                        raise RuntimeError("No model loaded in LM Studio.")
                    settings.model = ids[0]
                run_settings = AgentSettings(**{**settings.__dict__})
                run_settings.stream = True
                fast = bool(options.get("fast"))
                run_settings.critic = run_settings.critic and not fast
                run_settings.gap_check = run_settings.gap_check and not fast
                run_settings.learn = run_settings.learn and not fast
                run_settings.parallel = max(1, min(int(options.get("parallel") or settings.parallel), 8))
                agent = ResearchAgent(
                    state["client"], state["bridge"], run_settings, on_event=lambda e: run.push(decorate(e))
                )
                if options.get("quick") and not options.get("resume"):
                    await agent.quick(run.question)
                elif options.get("resume"):
                    await agent.run(project=run.project, resume=True)
                else:
                    await agent.run(run.question, project=run.project or None)
                run.status = "done"
            except asyncio.CancelledError:
                run.status = "cancelled"
            except Exception as e:
                run.status = "failed"
                run.push({"kind": "error", "text": f"{type(e).__name__}: {e}"})
            finally:
                run.push({"kind": "end", "status": run.status})

    # -- endpoints -------------------------------------------------------------------

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html")

    async def status(request: Request) -> Response:
        bridge = state["bridge"]
        return JSONResponse(
            {
                "error": state["error"],
                "model": settings.model,
                "base_url": settings.base_url,
                "servers": sorted(bridge.sessions) if bridge else [],
                "tools": len(bridge.tools) if bridge else 0,
                "workspace": str(workspace),
                "formats": list(FORMATS),
                "busy": state["lock"].locked(),
            }
        )

    async def create_run(request: Request) -> Response:
        if state["bridge"] is None:
            return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
        body = await request.json()
        question = str(body.get("question", "")).strip()
        project = str(body.get("project", "")).strip()
        if not question and not (body.get("resume") and project):
            return JSONResponse({"error": "question is required"}, status_code=400)
        run = Run(id=uuid.uuid4().hex[:12], question=question or f"(resume {project})", project=project)
        state["runs"][run.id] = run
        run.task = asyncio.create_task(execute(run, body))
        return JSONResponse(run.summary(), status_code=201)

    async def list_runs(request: Request) -> Response:
        return JSONResponse([r.summary() for r in reversed(list(state["runs"].values()))])

    async def run_events(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)

        async def stream():
            queue: asyncio.Queue = asyncio.Queue()
            past = list(run.events)
            run.listeners.append(queue)
            try:
                for event in past:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if past and past[-1]["kind"] == "end":
                    return
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["kind"] == "end":
                        return
            finally:
                run.listeners.remove(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def cancel_run(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None or run.task is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        run.task.cancel()
        return JSONResponse({"ok": True})

    async def list_projects(request: Request) -> Response:
        projects = []
        for p in (
            sorted(workspace.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if workspace.is_dir() else []
        ):
            if p.is_dir() and (p / "notes").is_dir():
                question = ""
                plan = p / "notes" / "plan.md"
                if plan.exists():
                    with contextlib.suppress(json.JSONDecodeError):
                        question = json.loads(plan.read_text(encoding="utf-8")).get("question", "")
                projects.append(
                    {
                        "name": p.name,
                        "question": question,
                        "has_report": (p / "report.md").exists(),
                        "modified": p.stat().st_mtime,
                    }
                )
        return JSONResponse(projects)

    async def get_project(request: Request) -> Response:
        try:
            pdir = project_dir(request.path_params["name"])
        except FileNotFoundError:
            return JSONResponse({"error": "unknown project"}, status_code=404)
        sources = (
            json.loads((pdir / "sources.json").read_text(encoding="utf-8")) if (pdir / "sources.json").exists() else []
        )
        plan = {}
        if (pdir / "notes" / "plan.md").exists():
            with contextlib.suppress(json.JSONDecodeError):
                plan = json.loads((pdir / "notes" / "plan.md").read_text(encoding="utf-8"))
        findings = []
        for note in sorted((pdir / "notes").glob("finding-*.md")):
            findings.append({"name": note.stem, "html": render_markdown(note.read_text(encoding="utf-8"))})
        report_html = ""
        if (pdir / "report.md").exists():
            body, refs = render_body((pdir / "report.md").read_text(encoding="utf-8"), sources, pdir)
            report_html = body + refs
        exports = sorted(f.name for f in (pdir / "exports").glob("report.*")) if (pdir / "exports").is_dir() else []
        return JSONResponse(
            {
                "name": pdir.name,
                "plan": plan,
                "sources": sources,
                "findings": findings,
                "report_html": report_html,
                "notebook": (pdir / "computations.ipynb").exists(),
                "exports": exports,
            }
        )

    async def export_project(request: Request) -> Response:
        fmt = request.query_params.get("format", "html")
        try:
            out = export(project_dir(request.path_params["name"]), fmt)
        except FileNotFoundError:
            return JSONResponse({"error": "unknown project"}, status_code=404)
        except ExportError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"path": str(out), "url": f"/files/{out.relative_to(workspace).as_posix()}"})

    def memory_db() -> sqlite3.Connection | None:
        path = workspace / "memory.sqlite"
        if not path.exists():
            return None
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    async def memory(request: Request) -> Response:
        query = request.query_params.get("q", "").strip()
        bridge = state["bridge"]
        if query and bridge and bridge.has("notebook__recall"):
            text, _ = await bridge.call("notebook__recall", {"query": query, "k": 20})
            return JSONResponse(parse_memory_lines(text))
        conn = memory_db()
        if conn is None:
            return JSONResponse([])
        with conn:
            rows = conn.execute("SELECT id, kind, text, sources FROM memories ORDER BY id DESC LIMIT 300").fetchall()
        return JSONResponse([dict(r) for r in rows])

    async def forget(request: Request) -> Response:
        bridge = state["bridge"]
        if bridge and bridge.has("notebook__forget"):
            text, err = await bridge.call("notebook__forget", {"memory_id": str(request.path_params["mid"])})
            return JSONResponse({"ok": not err and text == "Forgotten."})
        return JSONResponse({"ok": False}, status_code=503)

    async def skills(request: Request) -> Response:
        from local_ai.servers.notebook.skills import load_skills

        return JSONResponse([{k: v for k, v in s.items() if k != "body"} for s in load_skills().values()])

    async def files(request: Request) -> Response:
        path = (workspace / request.path_params["path"]).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    routes = [
        Route("/", index),
        Route("/api/status", status),
        Route("/api/runs", create_run, methods=["POST"]),
        Route("/api/runs", list_runs),
        Route("/api/runs/{run_id}/events", run_events),
        Route("/api/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route("/api/projects", list_projects),
        Route("/api/projects/{name}", get_project),
        Route("/api/projects/{name}/export", export_project, methods=["POST"]),
        Route("/api/memory", memory),
        Route("/api/memory/{mid:int}", forget, methods=["DELETE"]),
        Route("/api/skills", skills),
        Route("/files/{path:path}", files),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runs = state["runs"]
    return app


def main(argv: list[str]) -> int:
    import uvicorn

    p = argparse.ArgumentParser(prog="local-ai ui", description="Local web interface (127.0.0.1 only).")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--mcp-config", help="Path to mcp.json (default: LOCAL_AI_MCP_CONFIG, else ~/.lmstudio/mcp.json).")
    p.add_argument("--servers", help="Comma-separated subset of MCP servers.")
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--parallel", type=int, default=1)
    args = p.parse_args(argv)

    settings = AgentSettings(parallel=args.parallel)
    if args.base_url:
        settings.base_url = args.base_url
    if args.model:
        settings.model = args.model
    only = [s.strip() for s in args.servers.split(",")] if args.servers else None
    app = create_app(load_mcp_config(args.mcp_config, only), settings)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Research agent UI: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0

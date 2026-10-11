"""Local web interface for the research agent.

Runs on 127.0.0.1 only. Starts the MCP servers once, runs one research task at a time
(the compute kernel is shared) and streams the agent's events to the browser over SSE.

  local-ai ui [--port 8765] [--no-browser] [--mcp-config PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import webbrowser
from collections.abc import Callable
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
from .store import Store

STATIC = Path(__file__).parent / "static"


@dataclass
class Run:
    id: str
    question: str
    project: str
    status: str = "queued"  # queued | running | done | stopped | failed | cancelled | interrupted
    mode: str = "research"  # research | code
    title: str = ""  # chosen by the user; empty = shortened from the question
    folder: str | None = None  # id of the chat folder it is sorted into
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    agent: object | None = None  # the code agent of a code run, for the Compact button
    sink: Callable[[dict], None] | None = None  # saves events to disk
    loaded: bool = True  # False for a run read from disk whose events are not loaded yet

    def push(self, event: dict) -> None:
        if event["kind"] not in ("token", "stream_end"):  # live output is not replayed to late listeners
            self.events.append(event)
            if self.sink:
                self.sink(event)
        for q in self.listeners:
            q.put_nowait(event)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "project": self.project,
            "status": self.status,
            "mode": self.mode,
            "title": self.title,
            "folder": self.folder,
            "created": self.created,
        }


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


PICK_FOLDER = (
    "import sys, tkinter\n"
    "from tkinter import filedialog\n"
    "root = tkinter.Tk()\n"
    "root.withdraw()\n"
    "root.attributes('-topmost', True)\n"
    "print(filedialog.askdirectory(initialdir=sys.argv[1] or None, title='Project folder'))\n"
)


async def pick_folder_dialog(initial: str = "") -> str:
    """Open the operating system's folder dialog (the server runs on this computer) and return the chosen path,
    or "" when cancelled. It runs in its own process because tkinter needs a main thread."""
    initial_dir = initial if initial and Path(initial).expanduser().is_dir() else ""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        PICK_FOLDER,
        initial_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip().splitlines()[-1] if err.strip() else "dialog failed")
    return str(Path(out.decode().strip())) if out.strip() else ""


def code_available() -> bool:
    """Code mode is built-in."""
    return True


def create_app(servers: list[ServerConfig], settings: AgentSettings, client=None) -> Starlette:
    workspace = resolve_workspace(servers)
    os.environ.setdefault("LOCAL_AI_WORKSPACE", str(workspace))
    store = Store(workspace)
    state: dict = {"bridge": None, "client": client, "runs": {}, "lock": asyncio.Lock(), "error": None}

    def attach_store(run: Run) -> None:
        run.sink = lambda event: store.append_event(run.id, event)

    for run_id, meta in sorted(store.runs.items(), key=lambda item: item[1].get("created", 0)):
        past = Run(
            id=run_id,
            question=meta.get("question", ""),
            project=meta.get("project", ""),
            status="interrupted" if meta.get("status") in ("queued", "running") else meta.get("status", "done"),
            mode=meta.get("mode", "research"),
            title=meta.get("title", ""),
            folder=meta.get("folder"),
            created=meta.get("created", 0),
            loaded=False,
        )
        attach_store(past)
        state["runs"][run_id] = past
        if past.status != meta.get("status"):
            store.update_run(run_id, status=past.status)

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

    async def run_code(run: Run, options: dict, run_settings: AgentSettings) -> None:
        """Code mode: the local-coder agent edits a project folder and runs its tests until they pass."""
        from local_ai.coder import state as coder_state
        from local_ai.coder.agent.llm import LLM
        from local_ai.coder.agent.loop import CoderAgent
        from local_ai.coder.agent.tools import Tool

        root = Path(str(options.get("folder", ""))).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        task = run.question
        if not task or task.startswith("(continue"):
            task = "Continue the existing plan and finish the task."
        run.push({"kind": "log", "text": f"Project folder: {root}"})

        # Extra MCP tools based on selected servers
        extra_tools = {}
        bridge = state["bridge"]
        selected_servers = options.get("servers")
        if bridge:
            allowed = set(selected_servers) if selected_servers is not None else {"library", "compute", "web"}
            for name, btool in bridge.tools.items():
                if btool.server in allowed and btool.server != "coder":
                    def make_caller(tool_name):
                        async def _mcp_call(**kw):
                            text, is_err = await bridge.call(tool_name, kw)
                            return text if not is_err else f"Error: {text}"
                        return _mcp_call

                    extra_tools[name] = Tool(
                        name=name,
                        description=f"[{btool.server}] {btool.description}".strip(),
                        parameters=btool.input_schema,
                        fn=make_caller(name),
                    )

        if extra_tools:
            active_list = sorted({btool.server for btool in bridge.tools.values() if btool.public_name in extra_tools})
            run.push({"kind": "log", "text": f"Active MCP servers: {', '.join(active_list)} ({len(extra_tools)} tools)"})

        llm = LLM(
            state["client"],
            run_settings.model,
            temperature=run_settings.temperature,
            top_p=run_settings.top_p,
        )
        agent = CoderAgent(
            llm,
            root,
            emit=lambda kind, **data: run.push({"kind": kind, **data}),
            extra_tools=extra_tools,
        )
        run.agent = agent
        coder_state.moim_file().write_text(coder_state.IDLE, encoding="utf-8")
        outcome = await agent.run(task)
        run.push({"kind": "outcome", "finished": outcome.finished, "reason": outcome.reason})
        if outcome.summary:
            run.push({"kind": "answer", "text": outcome.summary, "html": render_markdown(outcome.summary)})
        run.status = "done" if outcome.finished else "stopped"

    async def execute(run: Run, options: dict) -> None:
        async with state["lock"]:
            run.status = "running"
            store.update_run(run.id, status="running")
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
                if options.get("servers") is not None:
                    run_settings.servers = list(options["servers"])
                if options.get("mode") == "code":
                    await run_code(run, options, run_settings)
                    return
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
                store.update_run(run.id, status=run.status)
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
                "code": code_available(),
            }
        )

    async def create_run(request: Request) -> Response:
        body = await request.json()
        if body.get("mode") != "code" and state["bridge"] is None:
            return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
        question = str(body.get("question", "")).strip()
        project = str(body.get("project", "")).strip()
        chat_folder = body.get("chat_folder") or None
        if not store.folder_exists(chat_folder):
            return JSONResponse({"error": "unknown folder"}, status_code=400)

        def start(run: Run) -> Response:
            run.folder, run.mode = chat_folder, "code" if body.get("mode") == "code" else "research"
            store.add_run(
                run.id,
                question=run.question,
                project=run.project,
                mode=run.mode,
                status=run.status,
                folder=chat_folder,
                created=run.created,
            )
            attach_store(run)
            state["runs"][run.id] = run
            run.task = asyncio.create_task(execute(run, body))
            return JSONResponse(run.summary(), status_code=201)

        if body.get("mode") == "code":
            folder = str(body.get("folder", "")).strip()
            if not code_available():
                return JSONResponse({"error": "Code mode needs local-coder: pip install -e ../local-coder"}, 503)
            if not folder or not Path(folder).expanduser().is_absolute():
                return JSONResponse({"error": "give the project folder as an absolute path"}, status_code=400)
            return start(Run(id=uuid.uuid4().hex[:12], question=question or "(continue the plan)", project=folder))
        if not question and not (body.get("resume") and project):
            return JSONResponse({"error": "question is required"}, status_code=400)
        return start(Run(id=uuid.uuid4().hex[:12], question=question or f"(resume {project})", project=project))

    async def list_runs(request: Request) -> Response:
        return JSONResponse([r.summary() for r in reversed(list(state["runs"].values()))])

    async def run_events(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        if not run.loaded:
            run.events = store.read_events(run.id)
            if not run.events or run.events[-1]["kind"] != "end":  # cut off by a restart
                run.events.append({"kind": "end", "status": run.status})
            run.loaded = True

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

    async def update_run(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        body = await request.json()
        fields = {}
        if "title" in body:
            fields["title"] = str(body["title"]).strip()[:120]
        if "folder" in body:
            if not store.folder_exists(body["folder"] or None):
                return JSONResponse({"error": "unknown folder"}, status_code=400)
            fields["folder"] = body["folder"] or None
        for key, value in fields.items():
            setattr(run, key, value)
        store.update_run(run.id, **fields)
        return JSONResponse(run.summary())

    async def delete_run(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        if run.status in ("queued", "running"):
            return JSONResponse({"error": "cancel the run before deleting it"}, status_code=409)
        del state["runs"][run.id]
        store.delete_run(run.id)
        return JSONResponse({"ok": True})

    async def list_folders(request: Request) -> Response:
        return JSONResponse(store.folders)

    async def create_folder(request: Request) -> Response:
        name = str((await request.json()).get("name", "")).strip()[:80]
        if not name:
            return JSONResponse({"error": "give the folder a name"}, status_code=400)
        return JSONResponse(store.add_folder(name), status_code=201)

    async def rename_folder(request: Request) -> Response:
        name = str((await request.json()).get("name", "")).strip()[:80]
        if not name:
            return JSONResponse({"error": "give the folder a name"}, status_code=400)
        if not store.rename_folder(request.path_params["fid"], name):
            return JSONResponse({"error": "unknown folder"}, status_code=404)
        return JSONResponse({"ok": True})

    async def delete_folder(request: Request) -> Response:
        fid = request.path_params["fid"]
        if not store.delete_folder(fid):
            return JSONResponse({"error": "unknown folder"}, status_code=404)
        for run in state["runs"].values():
            if run.folder == fid:
                run.folder = None
        return JSONResponse({"ok": True})

    async def compact_run(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        if run.agent is None or run.status != "running":
            return JSONResponse({"error": "only a running code task can be compacted"}, status_code=409)
        run.agent.request_compact()
        return JSONResponse({"ok": True})

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

    async def pick_folder(request: Request) -> Response:
        body = await request.json()
        try:
            path = await pick_folder_dialog(str(body.get("initial", "")).strip())
        except (RuntimeError, TimeoutError, OSError) as e:
            return JSONResponse({"error": f"Could not open the folder dialog ({e}). Type the path instead."}, 503)
        return JSONResponse({"path": path})

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
        Route("/api/runs/{run_id}", update_run, methods=["PATCH"]),
        Route("/api/runs/{run_id}", delete_run, methods=["DELETE"]),
        Route("/api/folders", list_folders),
        Route("/api/folders", create_folder, methods=["POST"]),
        Route("/api/folders/{fid}", rename_folder, methods=["PATCH"]),
        Route("/api/folders/{fid}", delete_folder, methods=["DELETE"]),
        Route("/api/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route("/api/runs/{run_id}/compact", compact_run, methods=["POST"]),
        Route("/api/projects", list_projects),
        Route("/api/projects/{name}", get_project),
        Route("/api/projects/{name}/export", export_project, methods=["POST"]),
        Route("/api/memory", memory),
        Route("/api/memory/{mid:int}", forget, methods=["DELETE"]),
        Route("/api/skills", skills),
        Route("/api/pick-folder", pick_folder, methods=["POST"]),
        Route("/files/{path:path}", files),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runs = state["runs"]
    app.state.store = store
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

"""Local web interface for the research agent and the coding agent.

Runs on 127.0.0.1 only. Starts the MCP servers once, runs one task at a time
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
import sys
import uuid
import webbrowser
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from local_ai.coder import files as coder_files
from local_ai.core.llm import pick_model
from local_ai.core.mcp_bridge import MCPBridge, ServerConfig, load_mcp_config
from local_ai.research.config import AgentSettings
from local_ai.servers.common import slugify, workspace_root
from local_ai.servers.export import FORMATS, ExportError, export, render_body, render_markdown

from .runs import ACTIVE, Run, clean_settings, run_code, run_research
from .store import Store

STATIC = Path(__file__).parent / "static"


def resolve_workspace(servers: list[ServerConfig]) -> Path:
    """Use the notebook server's workspace so the UI reads the same files it writes."""
    for s in servers:
        if s.name == "notebook" and s.env.get("LOCAL_AI_WORKSPACE"):
            return Path(s.env["LOCAL_AI_WORKSPACE"]).expanduser().resolve()
    return workspace_root()


MAX_TREE = 500  # files of a project listed for the file tree
PASSAGE_CHARS = 350  # of the tool output shown before and after a quote


def find_passage(evidence: str, quote: str) -> dict | None:
    """Where a registered quote stands in the saved tool output: the quote as written there, with the text
    around it. Spaces, line breaks and punctuation between the words may differ; None when it is not there."""
    words = re.findall(r"\w+", quote)
    if not words:
        return None
    for part in (words, words[:8]):  # the whole quote, else its start
        m = re.search(r"\W+".join(re.escape(w) for w in part), evidence, re.IGNORECASE)
        if m:
            return {
                "before": evidence[max(0, m.start() - PASSAGE_CHARS) : m.start()],
                "match": m.group(0),
                "after": evidence[m.end() : m.end() + PASSAGE_CHARS],
            }
    return None


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
            pinned=bool(meta.get("pinned")),
            settings=meta.get("settings") or {},
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

    async def execute(run: Run, options: dict, question: str, project: str = "") -> None:
        """Run one message of a chat: the first one, or a follow-up (project is the research project)."""
        async with state["lock"]:
            run.status = "running"
            run.agent = None  # the agent of an earlier message is gone
            store.update_run(run.id, status="running")
            run.push({"kind": "status", "status": "running"})
            try:
                if not settings.model and "model" not in run.settings:
                    settings.model = await pick_model(state["client"])
                    if not settings.model:
                        raise RuntimeError("No model loaded in LM Studio.")
                run_settings = AgentSettings(**{**settings.__dict__})
                run_settings.stream = True
                for key, value in run.settings.items():  # what this chat chose in the settings drawer
                    if hasattr(run_settings, key):
                        setattr(run_settings, key, value)
                if options.get("servers") is not None:
                    run_settings.servers = list(options["servers"])
                if options.get("mode") == "code":
                    await run_code(run, question, options, run_settings, state["client"], state["bridge"])
                else:
                    await run_research(
                        run,
                        question,
                        project,
                        options,
                        run_settings,
                        state["client"],
                        state["bridge"],
                        lambda e: run.push(decorate(e)),
                    )
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
            }
        )

    async def models(request: Request) -> Response:
        """The models LM Studio can serve, for the settings drawer."""
        try:
            listed = (await state["client"].models.list()).data
        except Exception as e:  # LM Studio's server is not running
            return JSONResponse({"models": [], "default": settings.model, "error": str(e)[:200]})
        ids = [m.id for m in listed if "embed" not in m.id.lower()]
        return JSONResponse({"models": ids, "default": settings.model})

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
            run.settings = clean_settings(body.get("settings"))
            store.add_run(
                run.id,
                question=run.question,
                project=run.project,
                mode=run.mode,
                status=run.status,
                folder=chat_folder,
                settings=run.settings,
                created=run.created,
            )
            attach_store(run)
            state["runs"][run.id] = run
            run.task = asyncio.create_task(execute(run, body, run.question, run.project))
            return JSONResponse(run.summary(), status_code=201)

        if body.get("mode") == "code":
            folder = str(body.get("folder", "")).strip()
            if not folder or not Path(folder).expanduser().is_absolute():
                return JSONResponse({"error": "give the project folder as an absolute path"}, status_code=400)
            return start(Run(id=uuid.uuid4().hex[:12], question=question or "(continue the plan)", project=folder))
        if not question and not (body.get("resume") and project):
            return JSONResponse({"error": "question is required"}, status_code=400)
        return start(Run(id=uuid.uuid4().hex[:12], question=question or f"(resume {project})", project=project))

    async def list_runs(request: Request) -> Response:
        return JSONResponse([r.summary() for r in reversed(list(state["runs"].values()))])

    def load_events(run: Run) -> None:
        """Read the events of a run of an earlier session from disk, once."""
        if run.loaded:
            return
        run.events = store.read_events(run.id)
        if not run.events or run.events[-1]["kind"] != "end":  # cut off by a restart
            run.events.append({"kind": "end", "status": run.status})
        run.loaded = True

    async def add_message(request: Request) -> Response:
        """A follow-up message in an existing chat: the agent runs again and its events join the same run."""
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        body = await request.json()
        message = str(body.get("question", "")).strip()
        if run.status in ACTIVE:
            if run.status == "running" and message and hasattr(run.agent, "steer"):
                run.agent.steer(message)  # a code run takes a message while it works
                run.push({"kind": "steer", "text": message})
                return JSONResponse(run.summary(), status_code=202)
            return JSONResponse({"error": "wait until the run has ended, or cancel it"}, status_code=409)
        if run.mode != "code":
            if not message:
                return JSONResponse({"error": "question is required"}, status_code=400)
            if state["bridge"] is None:
                return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
        load_events(run)
        options = {**body, "mode": run.mode, "folder": run.project, "resume": False}
        task = run.follow_up(message) if message else ""  # an empty message continues the plan of a code chat
        if "settings" in body:
            run.settings = clean_settings(body["settings"])
        run.status = "queued"
        store.update_run(run.id, status="queued", settings=run.settings)
        run.push({"kind": "user", "text": message or "(continue the plan)"})
        run.task = asyncio.create_task(execute(run, options, task))  # a research follow-up gets its own project
        return JSONResponse(run.summary(), status_code=202)

    async def run_events(request: Request) -> Response:
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        load_events(run)

        async def stream():
            queue: asyncio.Queue = asyncio.Queue()
            past = list(run.events)
            run.listeners.append(queue)
            try:
                for i, event in enumerate(past):
                    if event["kind"] == "end" and i < len(past) - 1:  # an earlier message of the chat ended here
                        event = {**event, "kind": "segment_end"}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if run.status not in ACTIVE:
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
        if "pinned" in body:
            fields["pinned"] = bool(body["pinned"])
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

    def code_root(request: Request) -> Path | None:
        """The project folder of a code chat."""
        run = state["runs"].get(request.path_params["run_id"])
        return Path(run.project).expanduser().resolve() if run is not None and run.mode == "code" else None

    async def run_files(request: Request) -> Response:
        """The files of a code chat's project, for the file tree."""
        root = code_root(request)
        if root is None:
            return JSONResponse({"error": "not a code chat"}, status_code=404)
        names = [n for n in (coder_files.walk(root) if root.is_dir() else []) if not n.endswith("/")]
        return JSONResponse({"root": str(root), "files": names[:MAX_TREE], "more": max(0, len(names) - MAX_TREE)})

    async def run_file(request: Request) -> Response:
        """One text file of a code chat's project (the start of a long one)."""
        root = code_root(request)
        path = request.query_params.get("path", "")
        if root is None or not path:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            target = coder_files.resolve(root, path)
        except ValueError:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content = coder_files.read_file(root, path)
        if content.startswith("Error: cannot read"):
            return JSONResponse({"error": "not a text file"}, status_code=415)
        return JSONResponse({"path": path, "content": content})

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

    async def source_passages(request: Request) -> Response:
        """The quotes of one source of a project, each with the passage of the tool output it was taken from."""
        try:
            pdir = project_dir(request.path_params["name"])
        except FileNotFoundError:
            return JSONResponse({"error": "unknown project"}, status_code=404)
        path = pdir / "sources.json"
        sources = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        source = next((s for s in sources if s["n"] == request.path_params["n"]), None)
        if source is None:
            return JSONResponse({"error": "unknown source"}, status_code=404)
        evidence = "\n\n".join(
            note.read_text(encoding="utf-8") for note in sorted((pdir / "notes").glob("evidence-*.md"))
        )
        passages = [{"quote": q, **(find_passage(evidence, q) or {})} for q in source.get("quotes", [])]
        return JSONResponse({"source": source, "passages": passages})

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
        Route("/api/models", models),
        Route("/api/runs", create_run, methods=["POST"]),
        Route("/api/runs", list_runs),
        Route("/api/runs/{run_id}/events", run_events),
        Route("/api/runs/{run_id}/messages", add_message, methods=["POST"]),
        Route("/api/runs/{run_id}", update_run, methods=["PATCH"]),
        Route("/api/runs/{run_id}", delete_run, methods=["DELETE"]),
        Route("/api/folders", list_folders),
        Route("/api/folders", create_folder, methods=["POST"]),
        Route("/api/folders/{fid}", rename_folder, methods=["PATCH"]),
        Route("/api/folders/{fid}", delete_folder, methods=["DELETE"]),
        Route("/api/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route("/api/runs/{run_id}/compact", compact_run, methods=["POST"]),
        Route("/api/runs/{run_id}/files", run_files),
        Route("/api/runs/{run_id}/file", run_file),
        Route("/api/projects", list_projects),
        Route("/api/projects/{name}", get_project),
        Route("/api/projects/{name}/export", export_project, methods=["POST"]),
        Route("/api/projects/{name}/sources/{n:int}/passages", source_passages),
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
    print(f"Local AI UI: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0

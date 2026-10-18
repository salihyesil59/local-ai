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
import shlex
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
from local_ai.core import attachments as att
from local_ai.core import images as img
from local_ai.core.llm import pick_model
from local_ai.core.mcp_bridge import (
    TOOL_DEFAULTS,
    MCPBridge,
    ServerConfig,
    load_mcp_config,
    read_servers,
    server_config,
    write_server,
)
from local_ai.core.mcp_bridge import config_path as mcp_config_path
from local_ai.research.config import AgentSettings
from local_ai.servers.common import NOTEBOOKS, slugify, workspace_root
from local_ai.servers.export import FORMATS, ExportError, export, render_body, render_markdown

from . import offline
from .runs import (
    ACTIVE,
    FILES_ONLY,
    IMAGES_ONLY,
    NOTEBOOK_NOTE,
    Run,
    clean_settings,
    run_chat,
    run_code,
    run_research,
)
from .store import Store

STATIC = Path(__file__).parent / "static"
# Characters of attached files that go into a message; a research repeats its question in every step.
ATTACH_CHARS = 40_000
ATTACH_RESEARCH_CHARS = 12_000
MAX_RUNS = 4  # chats working at the same time, any mode: the parallel requests LM Studio serves by default
REVALIDATE = {"Cache-Control": "no-cache"}  # the browser asks each time, so a new version of the UI is used at once


class UIFiles(StaticFiles):
    """The files of the UI, never taken from the browser's cache unchecked (an unchanged file costs a 304)."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


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


SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


def parse_mcp_server(body: dict) -> tuple[str, dict]:
    """A server to add, from the form of the UI: (name, its entry for mcp.json). Either the fields name + command
    (a command line) or url, with env as {"KEY": "value"}; or json, the snippet an MCP server's README gives:
    {"mcpServers": {"name": {...}}} or {"name": {...}} or, with the name field, the entry itself."""
    name = str(body.get("name", "")).strip()
    pasted = str(body.get("json", "")).strip()
    if pasted:
        try:
            data = json.loads(pasted)
        except json.JSONDecodeError as e:
            raise ValueError(f"the JSON cannot be read: {e}") from None
        if not isinstance(data, dict):
            raise ValueError("the JSON must be an object")
        data = data.get("mcpServers", data)
        if "command" in data or "url" in data:
            spec = data
        elif len(data) == 1 and isinstance(next(iter(data.values())), dict):
            name, spec = next(iter(data.items()))
        else:
            raise ValueError("the JSON must describe one server")
    else:
        command, url = str(body.get("command", "")).strip(), str(body.get("url", "")).strip()
        spec = {}
        if command:
            parts = [p.strip('"') for p in shlex.split(command, posix=os.name != "nt")]
            spec = {"command": parts[0], "args": parts[1:]}
        elif url:
            spec = {"url": url}
        env = body.get("env") or {}
        if isinstance(env, dict) and env:
            spec["env"] = {str(k): str(v) for k, v in env.items()}
    if not SERVER_NAME.fullmatch(name):
        raise ValueError("give the server a name of letters, digits, - or _")
    if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
        raise ValueError("give a command to start the server, or its URL")
    if spec.get("url") and not str(spec["url"]).startswith(("http://", "https://")):
        raise ValueError("the URL must start with http:// or https://")
    return name, spec


def create_app(
    servers: list[ServerConfig], settings: AgentSettings, client=None, config_path: Path | None = None
) -> Starlette:
    """config_path: the mcp.json the servers came from; with it the UI can add, disable and remove servers."""
    workspace = resolve_workspace(servers)
    os.environ.setdefault("LOCAL_AI_WORKSPACE", str(workspace))
    store = Store(workspace)
    # slots: runs that work at the same time, any mode (LM Studio answers that many requests at once; more wait);
    # folders: a code run waits for another one in the same project folder; active: runs working now
    state: dict = {
        "bridge": None, "client": client, "runs": {}, "error": None,
        "slots": asyncio.Semaphore(MAX_RUNS), "folders": {}, "active": 0,
    }  # fmt: skip
    zim_job = offline.ZimJob()

    def where() -> offline.Places:
        return offline.places(config_path)

    def attach_store(run: Run) -> None:
        run.sink = lambda event: store.append_event(run.id, event)

    def put_in_library(run_id: str, item: dict) -> str:
        """The whole of a large attached file in the chat's notebook of the library (Word, PowerPoint, ... as the
        text read from them); its path there, "" when it could not be saved."""
        name, content = item["name"], item["data"]
        if Path(name).suffix.lower() not in offline.LIBRARY_SUFFIXES:
            name, content = f"{name}.md", item["text"].encode("utf-8")
        try:
            return offline.save_file(where(), f"{NOTEBOOKS}/ekler-{run_id}", name, content)["path"]
        except (OSError, ValueError):
            return ""

    async def take_files(run: Run, uploads: list[tuple[str, bytes]]) -> tuple[list[str], dict]:
        """Keep the files of a message and make their text for the agent: their names in the chat, and the options
        that bring the text to the run (what does not fit is cut there; the whole file goes into the library)."""
        if not uploads:
            return [], {}
        names = [store.save_file(run.id, name, data) for name, data in uploads]
        items = []
        for (name, data), stored in zip(uploads, names, strict=True):
            try:
                text, info = await asyncio.to_thread(att.extract, store.file_path(run.id, stored))
            except Exception as e:  # a damaged file: the model is told that nothing could be read from it
                text, info = "", f"unreadable: {type(e).__name__}"
            items.append({"name": name, "text": text, "info": info, "data": data})
        budget = ATTACH_RESEARCH_CHARS if run.mode == "research" else ATTACH_CHARS
        in_library = False
        for item, share in zip(items, att.shares([len(i["text"]) for i in items], budget), strict=True):
            if len(item["text"]) > share:
                item["library"] = await asyncio.to_thread(put_in_library, run.id, item)
                in_library = in_library or bool(item["library"])
        return names, {"attachment_text": att.block(items, budget), "attachment_library": in_library}

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
            images=list(meta.get("images") or []),
            files=list(meta.get("files") or []),
            notebook=meta.get("notebook") or "",
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
            # not strict: a server that does not start is shown with its error, the others run
            async with MCPBridge(servers, strict=False) as bridge:
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
        """Run one message of a chat: the first one, or a follow-up (project is the research project). Chats of any
        mode work at the same time, up to MAX_RUNS; a code run waits for another one in the same project folder."""
        folder = None
        if options.get("mode") == "code":
            key = str(Path(str(options.get("folder", ""))).expanduser().resolve()).lower()
            folder = state["folders"].setdefault(key, asyncio.Lock())
        async with state["slots"], folder or contextlib.nullcontext():
            state["active"] += 1
            try:
                await work(run, options, question, project)
            finally:
                state["active"] -= 1

    async def work(run: Run, options: dict, question: str, project: str) -> None:
        """One message of a chat, with its events and the status of the run."""
        run.status = "running"
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
            notebook, note = options.get("notebook") or "", ""
            if notebook and options.get("mode") != "code":
                # search_library looks only into the notebook's folder, unless the model asks for more
                files = offline.notebook_files(where(), notebook)
                # for this run's task only: runs at the same time have their own
                TOOL_DEFAULTS.set({"library__search_library": {"scope": f"notebooks/{notebook}"}})
                if run_settings.servers is not None and "library" not in run_settings.servers:  # None: all
                    run_settings.servers = [*run_settings.servers, "library"]
                note = NOTEBOOK_NOTE.format(name=notebook, files=", ".join(files) or "(none yet)")
                question += note
                run.push({"kind": "log", "text": f"Notebook: {notebook} ({len(files)} files)"})
            if options.get("attachment_text"):  # the text of the files sent with this message
                question += options["attachment_text"]
                servers = run_settings.servers
                if options.get("attachment_library") and servers is not None and "library" not in servers:
                    run_settings.servers = [*servers, "library"]
            if options.get("mode") == "code":
                await run_code(run, question, options, run_settings, state["client"], state["bridge"], store)
            elif options.get("mode") == "chat":
                await run_chat(run, question, options, run_settings, state["client"], state["bridge"], store)
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
                asked = run.last_question()
                while run.queued:  # sent while it ran: researched now, each with the answer before it
                    message, paths = run.queued.pop(0)
                    run.push({"kind": "dequeued"})
                    run.push({"kind": "log", "text": f"Now the question sent during the research: {message}"})
                    task = run.follow_up(message, asked) + note
                    follow = {**options, "image_paths": paths, "resume": False}
                    await run_research(
                        run, task, "", follow, run_settings, state["client"], state["bridge"],
                        lambda e: run.push(decorate(e)),
                    )  # fmt: skip
                    asked = message
        except FileNotFoundError as e:
            run.status = "failed"
            run.push({"kind": "error", "text": f"No notebook {e}: it was deleted or renamed."})
        except asyncio.CancelledError:
            run.status = "cancelled"
        except Exception as e:
            run.status = "failed"
            run.push({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            if run.queued:  # the research was stopped before it came to them
                text = f"{len(run.queued)} message(s) sent during the research were not researched; send them again."
                run.push({"kind": "log", "level": "warning", "text": text})
                run.queued.clear()
            store.update_run(run.id, status=run.status)
            run.push({"kind": "end", "status": run.status})

    # -- endpoints -------------------------------------------------------------------

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers=REVALIDATE)

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
                "busy": state["active"] > 0,
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

    # -- MCP servers: what is configured, what runs, adding and removing ----------------------------

    def mcp_entry(name: str, spec: dict | None = None) -> dict:
        bridge = state["bridge"]
        running = next((s for s in (bridge.servers if bridge else []) if s.name == name), None)
        spec = spec if spec is not None else ({"command": running.command, "args": running.args, "url": running.url})
        connected = bool(bridge and name in bridge.sessions)
        tools = [t for t in bridge.tools.values() if t.server == name] if bridge else []
        return {
            "name": name,
            "command": " ".join([spec.get("command") or "", *map(str, spec.get("args") or [])]).strip(),
            "url": spec.get("url") or "",
            "env": sorted(dict(spec.get("env") or {})),  # the names only: values may be keys or tokens
            "disabled": bool(spec.get("disabled")),
            "connected": connected,
            "error": "" if connected or not bridge else bridge.errors.get(name, ""),
            "tools": [{"name": t.tool, "description": " ".join(t.description.split())[:200]} for t in tools],
            "required": name == settings.notebook_server,
        }

    def mcp_servers() -> list[dict]:
        configured = read_servers(config_path) if config_path else {}
        entries = [mcp_entry(name, spec) for name, spec in configured.items()]
        bridge = state["bridge"]
        entries += [mcp_entry(s.name) for s in (bridge.servers if bridge else []) if s.name not in configured]
        return entries

    async def list_mcp(request: Request) -> Response:
        return JSONResponse(
            {"config": str(config_path) if config_path else "", "editable": bool(config_path), "servers": mcp_servers()}
        )

    def mcp_refused(name: str | None = None) -> Response | None:
        """Why the server list cannot be changed right now, or None."""
        if not config_path:
            return JSONResponse({"error": "this UI was started without an mcp.json to change"}, status_code=400)
        if state["bridge"] is None:
            return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
        if state["active"]:
            return JSONResponse({"error": "wait until the runs have ended"}, status_code=409)
        if name is not None and name not in read_servers(config_path):
            return JSONResponse({"error": "unknown server"}, status_code=404)
        return None

    async def mcp_start(name: str, spec: dict) -> None:
        """Start (or stop, when disabled) a server as its entry in the file says; a failure stays in bridge.errors."""
        bridge = state["bridge"]
        if spec.get("disabled"):
            await bridge.disconnect(name)
            bridge.errors.pop(name, None)
            return
        with contextlib.suppress(Exception):
            await bridge.connect(server_config(name, spec))

    async def add_mcp(request: Request) -> Response:
        refused = mcp_refused()
        if refused:
            return refused
        try:
            name, spec = parse_mcp_server(await request.json())
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        write_server(config_path, name, spec)
        await mcp_start(name, spec)
        return JSONResponse(mcp_entry(name, spec), status_code=201)

    async def update_mcp(request: Request) -> Response:
        name = request.path_params["name"]
        refused = mcp_refused(name)
        if refused:
            return refused
        body = await request.json()
        spec = read_servers(config_path)[name]
        if "disabled" in body:
            if body["disabled"] and name == settings.notebook_server:
                return JSONResponse({"error": "the research agent needs this server"}, status_code=400)
            spec = {**spec, "disabled": bool(body["disabled"])}
            if not spec["disabled"]:
                del spec["disabled"]
            write_server(config_path, name, spec)
        await mcp_start(name, spec)  # also the way to restart a server
        return JSONResponse(mcp_entry(name, spec))

    async def delete_mcp(request: Request) -> Response:
        name = request.path_params["name"]
        refused = mcp_refused(name)
        if refused:
            return refused
        if name == settings.notebook_server:
            return JSONResponse({"error": "the research agent needs this server"}, status_code=400)
        await state["bridge"].disconnect(name)
        state["bridge"].errors.pop(name, None)
        state["bridge"].servers = [s for s in state["bridge"].servers if s.name != name]
        write_server(config_path, name, None)
        return JSONResponse({"ok": True})

    async def create_run(request: Request) -> Response:
        body = await request.json()
        if body.get("mode") not in ("code", "chat") and state["bridge"] is None:
            return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
        question = str(body.get("question", "")).strip()
        project = str(body.get("project", "")).strip()
        chat_folder = body.get("chat_folder") or None
        if not store.folder_exists(chat_folder):
            return JSONResponse({"error": "unknown folder"}, status_code=400)
        try:
            pictures = img.decode(body.get("images"))
            uploads = att.decode(body.get("files"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if pictures and not question:
            question = IMAGES_ONLY
        elif uploads and not question:
            question = FILES_ONLY

        async def start(run: Run) -> Response:
            run.folder, run.mode = chat_folder, body.get("mode") if body.get("mode") in ("code", "chat") else "research"
            run.settings = clean_settings(body.get("settings"))
            run.images = [store.save_image(run.id, data, ext) for data, ext in pictures]
            run.files, attached = await take_files(run, uploads)
            run.notebook = str(body.get("notebook") or "")
            store.add_run(
                run.id,
                question=run.question,
                project=run.project,
                mode=run.mode,
                status=run.status,
                folder=chat_folder,
                settings=run.settings,
                created=run.created,
                images=run.images,
                files=run.files,
                notebook=run.notebook,
            )
            attach_store(run)
            state["runs"][run.id] = run
            paths = [store.image_path(run.id, n) for n in run.images]
            options = {**body, "images": None, "files": None, "image_paths": paths, **attached}
            run.task = asyncio.create_task(execute(run, options, run.question, run.project))
            return JSONResponse(run.summary(), status_code=201)

        if body.get("mode") == "code":
            folder = str(body.get("folder", "")).strip()
            if not folder or not Path(folder).expanduser().is_absolute():
                return JSONResponse({"error": "give the project folder as an absolute path"}, status_code=400)
            return await start(Run(id=uuid.uuid4().hex[:12], question=question or "(continue the plan)", project=folder))
        if body.get("mode") == "chat":
            if not question:
                return JSONResponse({"error": "write a message"}, status_code=400)
            return await start(Run(id=uuid.uuid4().hex[:12], question=question, project=""))
        if not question and not (body.get("resume") and project):
            return JSONResponse({"error": "question is required"}, status_code=400)
        return await start(Run(id=uuid.uuid4().hex[:12], question=question or f"(resume {project})", project=project))

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
        try:
            pictures = img.decode(body.get("images"))
            uploads = att.decode(body.get("files"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if pictures and not message:
            message = IMAGES_ONLY
        elif uploads and not message:
            message = FILES_ONLY

        async def keep() -> tuple[dict, dict]:
            """Save the images and files of this message: what the user event shows (their names), and the
            options that bring the text of the files to the agent."""
            names = [store.save_image(run.id, data, ext) for data, ext in pictures]
            files, attached = await take_files(run, uploads)
            return {**({"images": names} if names else {}), **({"files": files} if files else {})}, attached

        if run.status in ACTIVE:
            if run.status == "running" and message and run.mode == "research":
                shown, attached = await keep()  # researched when the running research is done
                paths = [store.image_path(run.id, n) for n in shown.get("images", [])]
                run.queued.append((message + attached.get("attachment_text", ""), paths))
                run.push({"kind": "steer", "text": message, **shown})
                return JSONResponse(run.summary(), status_code=202)
            if run.status == "running" and message and hasattr(run.agent, "steer"):
                shown, attached = await keep()
                paths = [store.image_path(run.id, n) for n in shown.get("images", [])]
                run.agent.steer(message + attached.get("attachment_text", ""), paths)
                run.push({"kind": "steer", "text": message, **shown})  # a code or chat run takes it while it works
                return JSONResponse(run.summary(), status_code=202)
            return JSONResponse({"error": "wait until the run has ended, or cancel it"}, status_code=409)
        load_events(run)
        project = ""
        if run.mode == "chat" and not message:
            return JSONResponse({"error": "write a message"}, status_code=400)
        if run.mode == "research":
            if state["bridge"] is None:
                return JSONResponse({"error": state["error"] or "MCP servers not ready"}, status_code=503)
            if not message:  # go on with the research that was stopped, from its saved plan and findings
                project = run.resumable()
                if not project:
                    return JSONResponse({"error": "question is required"}, status_code=400)
        shown, attached = await keep()
        return follow_up(run, body, message, project, shown.get("images", []), files=shown.get("files", []),
                         attached=attached)  # fmt: skip

    def follow_up(
        run: Run,
        body: dict,
        message: str,
        project: str,
        images: list[str],
        shown: bool = True,
        files: list[str] | None = None,
        attached: dict | None = None,
    ) -> Response:
        """Start the next message of a chat (images, files: names of its saved images and files; attached: the
        options with the text of the files). shown=False: the message is on the page already (it was queued while
        the chat worked), the user event only starts its part."""
        paths = [store.image_path(run.id, n) for n in images]
        options = {**body, "images": None, "files": None, "image_paths": paths, "mode": run.mode, "folder": run.project}
        options.update(attached or {})
        options["resume"] = bool(project)
        task = run.follow_up(message) if message else ""  # an empty message continues the plan of a code chat
        if run.mode in ("code", "chat"):  # the agent still has the conversation: the message alone is enough
            options["with_earlier_answer"], task = task, message
        if "settings" in body:
            run.settings = clean_settings(body["settings"])
        run.notebook = str(body.get("notebook") or "")
        run.status = "queued"
        store.update_run(run.id, status="queued", settings=run.settings, notebook=run.notebook)
        text = message or ("(continue the research)" if project else "(continue the plan)")
        run.push(
            {
                "kind": "user",
                "text": text,
                **({"images": images} if images else {}),
                **({"files": files} if files else {}),
                **({} if shown else {"queued": True}),
            }
        )
        # a research follow-up with a question gets a project of its own; without one the stopped project goes on
        run.task = asyncio.create_task(execute(run, options, task, project))
        return JSONResponse(run.summary(), status_code=202)

    async def send_now(request: Request) -> Response:
        """The messages a chat queued while it worked, answered now: what it is doing is stopped (what it has done
        stays, as after Stop) and they become its next message. The body has the options of a message."""
        run = state["runs"].get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        pending = run.queued if run.mode == "research" else getattr(run.agent, "steering", None)
        if run.status != "running" or not pending:
            return JSONResponse({"error": "the message is being answered already"}, status_code=409)
        items = list(pending)
        pending.clear()
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        message = "\n\n".join(text for text, _ in items)
        images = [path.name for _, paths in items for path in paths]
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return follow_up(run, body, message, "", images, shown=False)

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
        with contextlib.suppress(FileNotFoundError):  # the large files it put into the library
            offline.delete_notebook(where(), f"ekler-{run.id}")
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
        documents = (
            sorted(f.name for f in (pdir / "exports").glob("document.*")) if (pdir / "exports").is_dir() else []
        )
        return JSONResponse(
            {
                "name": pdir.name,
                "plan": plan,
                "sources": sources,
                "findings": findings,
                "report_html": report_html,
                "notebook": (pdir / "computations.ipynb").exists(),
                "exports": exports,
                "documents": documents,
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

    # -- the Offline view: library files, notebooks, ZIM archives ---------------------------------------

    async def offline_state(request: Request) -> Response:
        places = where()
        listing = await asyncio.to_thread(offline.library_listing, places)
        archives = await asyncio.to_thread(offline.zim_listing, places)
        zims = {"dirs": [str(d) for d in places.zim_dirs], "archives": archives}
        return JSONResponse({"library": listing, "zim": zims, "job": zim_job.state})

    async def offline_upload(request: Request) -> Response:
        """A file into the library: the body is the file, folder and name are in the query."""
        if int(request.headers.get("content-length") or 0) > offline.MAX_UPLOAD:
            return JSONResponse({"error": "the file is too large"}, status_code=413)
        content = await request.body()
        try:
            entry = await asyncio.to_thread(
                offline.save_file, where(), request.query_params.get("folder", ""),
                request.query_params.get("name", ""), content,
            )  # fmt: skip
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(entry, status_code=201)

    async def offline_delete(request: Request) -> Response:
        try:
            offline.delete_file(where(), request.query_params.get("path", ""))
        except (ValueError, FileNotFoundError):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})

    async def create_notebook(request: Request) -> Response:
        try:
            name = offline.create_notebook(where(), str((await request.json()).get("name", "")))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"name": name}, status_code=201)

    async def delete_notebook(request: Request) -> Response:
        try:
            offline.delete_notebook(where(), request.path_params["name"])
        except FileNotFoundError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})

    async def zim_updates(request: Request) -> Response:
        try:
            found = await asyncio.to_thread(offline.zim_updates, where())
        except Exception as e:  # no internet, or the catalog did not answer
            return JSONResponse({"error": f"Could not reach the Kiwix catalog: {e}"}, status_code=502)
        return JSONResponse(found)

    async def zim_update(request: Request) -> Response:
        """Download the newer releases of the chosen archives (the catalog is asked again: no URL from outside)."""
        body = await request.json()
        wanted = set(body.get("files") or [])
        try:
            found = await asyncio.to_thread(offline.zim_updates, where())
        except Exception as e:
            return JSONResponse({"error": f"Could not reach the Kiwix catalog: {e}"}, status_code=502)
        chosen = [u for u in found if not wanted or u["file"] in wanted]
        if not chosen:
            return JSONResponse({"error": "no update for these archives"}, status_code=400)
        try:
            zim_job.start(chosen, keep_old=bool(body.get("keep_old")))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(zim_job.state, status_code=202)

    async def zim_cancel(request: Request) -> Response:
        zim_job.cancelled.set()
        return JSONResponse(zim_job.state)

    async def run_image(request: Request) -> Response:
        """An image sent with a message of a chat."""
        path = store.image_path(request.path_params["run_id"], request.path_params["name"])
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    async def run_attachment(request: Request) -> Response:
        """A file sent with a message of a chat (a download, under its own name)."""
        name = request.path_params["name"]
        path = store.file_path(request.path_params["run_id"], name)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path, filename=name.split("-", 1)[1])

    async def files(request: Request) -> Response:
        path = (workspace / request.path_params["path"]).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    routes = [
        Route("/", index),
        Route("/api/status", status),
        Route("/api/models", models),
        Route("/api/mcp", list_mcp),
        Route("/api/mcp", add_mcp, methods=["POST"]),
        Route("/api/mcp/{name}", update_mcp, methods=["PATCH"]),
        Route("/api/mcp/{name}", delete_mcp, methods=["DELETE"]),
        Route("/api/runs", create_run, methods=["POST"]),
        Route("/api/runs", list_runs),
        Route("/api/runs/{run_id}/events", run_events),
        Route("/api/runs/{run_id}/messages", add_message, methods=["POST"]),
        Route("/api/runs/{run_id}/send-now", send_now, methods=["POST"]),
        Route("/api/runs/{run_id}/images/{name}", run_image),
        Route("/api/runs/{run_id}/attachments/{name}", run_attachment),
        Route("/api/offline", offline_state),
        Route("/api/offline/files", offline_upload, methods=["POST"]),
        Route("/api/offline/files", offline_delete, methods=["DELETE"]),
        Route("/api/offline/notebooks", create_notebook, methods=["POST"]),
        Route("/api/offline/notebooks/{name}", delete_notebook, methods=["DELETE"]),
        Route("/api/offline/zim/updates", zim_updates),
        Route("/api/offline/zim/update", zim_update, methods=["POST"]),
        Route("/api/offline/zim/cancel", zim_cancel, methods=["POST"]),
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
        Mount("/static", UIFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runs = state["runs"]
    app.state.slots = state["slots"]  # the runs that may work at the same time
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
    app = create_app(load_mcp_config(args.mcp_config, only), settings, config_path=mcp_config_path(args.mcp_config))
    url = f"http://127.0.0.1:{args.port}"
    print(f"Local AI UI: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0

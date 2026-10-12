"""Bridge between MCP servers and an OpenAI-compatible tool-calling model.

Every tool of every configured server is exposed to the model as
`<server>__<tool>` (e.g. `library__search`, `notebook__write_note`),
so tools with the same name on different servers never collide. The server list comes from an mcp.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from local_ai.core.tools import Tool, parse_arguments

DEFAULT_MCP_CONFIG = Path("~/.lmstudio/mcp.json").expanduser()
SEPARATOR = "__"


@dataclass
class ServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def load_mcp_config(path: str | os.PathLike | None = None, only: list[str] | None = None) -> list[ServerConfig]:
    """Read an mcp.json in the LM Studio / Cursor format:

    {"mcpServers": {"library": {"command": "local-ai-library", "args": [], "env": {}}}}

    Default: LOCAL_AI_MCP_CONFIG, else LM Studio's ~/.lmstudio/mcp.json. Disabled servers are left out.
    """
    path = config_path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"MCP config not found: {path}. Pass --mcp-config or create it from config/lmstudio/mcp.json.example."
        )
    return [
        server_config(name, spec)
        for name, spec in read_servers(path).items()
        if not (only and name not in only) and not spec.get("disabled")
    ]


def config_path(path: str | os.PathLike | None = None) -> Path:
    """The mcp.json in use: the given one, else LOCAL_AI_MCP_CONFIG, else LM Studio's ~/.lmstudio/mcp.json."""
    path = path or os.environ.get("LOCAL_AI_MCP_CONFIG")
    return Path(path).expanduser() if path else DEFAULT_MCP_CONFIG


def server_config(name: str, spec: dict) -> ServerConfig:
    return ServerConfig(
        name=name,
        command=spec.get("command"),
        args=[str(a) for a in spec.get("args", [])],
        env={str(k): str(v) for k, v in dict(spec.get("env", {})).items()},
        url=spec.get("url"),
        headers=dict(spec.get("headers", {})),
    )


def read_servers(path: Path) -> dict[str, dict]:
    """Every server of an mcp.json as written there (also the disabled ones); {} when the file does not exist."""
    if not path.is_file():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}))


def write_server(path: Path, name: str, spec: dict | None) -> None:
    """Add or replace one server in an mcp.json (spec None removes it); the rest of the file stays as it is."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    servers = data.setdefault("mcpServers", {})
    if spec is None:
        servers.pop(name, None)
    else:
        servers[name] = spec
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def describe(error: BaseException) -> str:
    """One line for why a server did not start (errors of a task group come wrapped in a group)."""
    while getattr(error, "exceptions", None):  # an exception group (Python 3.11+, or the backport)
        error = error.exceptions[0]
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text[:300]}" if text else type(error).__name__


def _field(obj, camel: str, snake: str, default=None):
    """mcp 1.x uses camelCase model fields, mcp 2.x snake_case; accept both."""
    return getattr(obj, camel, getattr(obj, snake, default))


@dataclass
class BridgedTool:
    public_name: str  # name shown to the model
    server: str
    tool: str  # name on the MCP server
    description: str
    input_schema: dict


def public_tool_name(server: str, tool: str) -> str:
    """OpenAI tool names must match ^[a-zA-Z0-9_-]{1,64}$."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}{SEPARATOR}{tool}")
    return name[:64]


class MCPBridge:
    """The running MCP servers and their tools. Every server lives in a task of its own, so one can be started or
    stopped while the others keep running (the web UI adds and removes servers)."""

    def __init__(self, servers: list[ServerConfig], strict: bool = True):
        self.servers = list(servers)
        self.strict = strict  # False: a server that does not start is noted in errors and the others run
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, BridgedTool] = {}
        self.errors: dict[str, str] = {}  # server -> why it is not running
        self._running: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}
        # Read-only tools whose results may be reused within a run (set by the agent).
        self.cacheable: set[str] = set()
        self._cache: dict[tuple[str, str], asyncio.Future] = {}
        self.cache_hits = 0

    async def __aenter__(self) -> MCPBridge:
        try:
            for server in self.servers:
                try:
                    await self.connect(server)
                except Exception:
                    if self.strict:
                        raise
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        for name in reversed(list(self._running)):
            await self.disconnect(name)

    async def connect(self, server: ServerConfig) -> None:
        """Start a server and list its tools; a server of the same name is stopped first. On failure the reason is
        kept in errors and raised."""
        await self.disconnect(server.name)
        self.errors.pop(server.name, None)
        self.servers = [s for s in self.servers if s.name != server.name] + [server]
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._serve(server, ready, stop))
        self._running[server.name] = (task, stop)
        try:
            await ready
        except BaseException as e:
            self._running.pop(server.name, None)
            await asyncio.gather(task, return_exceptions=True)
            self.errors[server.name] = describe(e)
            raise

    async def disconnect(self, name: str) -> None:
        """Stop a server; its tools are gone when this returns."""
        running = self._running.pop(name, None)
        if running:
            task, stop = running
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    async def _serve(self, server: ServerConfig, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """The life of one server: connect, list the tools, then wait until it is stopped. The connection is
        opened and closed in this task, as its context managers require."""
        try:
            async with AsyncExitStack() as stack:
                if server.command:
                    params = StdioServerParameters(
                        command=server.command, args=server.args, env={**os.environ, **server.env}
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                elif server.url:
                    from mcp.client.streamable_http import streamablehttp_client

                    read, write, _ = await stack.enter_async_context(
                        streamablehttp_client(server.url, headers=server.headers or None)
                    )
                else:
                    raise ValueError(f"MCP server {server.name!r} has neither 'command' nor 'url'.")
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                result = await session.list_tools()
                self.sessions[server.name] = session
                for tool in result.tools:
                    name = public_tool_name(server.name, tool.name)
                    self.tools[name] = BridgedTool(
                        public_name=name,
                        server=server.name,
                        tool=tool.name,
                        description=tool.description or "",
                        input_schema=_field(tool, "inputSchema", "input_schema")
                        or {"type": "object", "properties": {}},
                    )
                ready.set_result(None)
                await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            elif isinstance(e, Exception):
                self.errors[server.name] = f"stopped: {describe(e)}"
            if not isinstance(e, Exception):
                raise
        finally:
            self.sessions.pop(server.name, None)
            for name in [n for n, t in self.tools.items() if t.server == server.name]:
                del self.tools[name]

    def has(self, name: str) -> bool:
        return name in self.tools

    def openai_tools(self, only_servers: list[str] | set[str] | None = None) -> list[dict]:
        servers_filter = set(only_servers) if only_servers is not None else None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.public_name,
                    "description": f"[{t.server}] {t.description}".strip(),
                    "parameters": t.input_schema,
                },
            }
            for t in self.tools.values()
            if servers_filter is None or t.server in servers_filter
        ]

    def as_tools(self, only_servers: list[str] | set[str] | None = None) -> dict[str, Tool]:
        """The bridged tools as functions to call in-process (for the coding agent); errors come back as text."""

        def caller(name: str):
            async def call(**arguments) -> str:
                return (await self.call(name, arguments))[0]

            return call

        return {
            spec["function"]["name"]: Tool(fn=caller(spec["function"]["name"]), **spec["function"])
            for spec in self.openai_tools(only_servers)
        }

    async def call(self, name: str, arguments: dict | str | None) -> tuple[str, bool]:
        """Call a bridged tool. Returns (text, is_error). Never raises for
        model mistakes: errors come back as text so the model can retry."""
        tool = self.tools.get(name)
        if tool is None:
            return (
                f"Error: unknown tool {name!r}. Available tools: {', '.join(sorted(self.tools))}",
                True,
            )
        arguments, problem = parse_arguments(arguments)
        if problem:
            return problem, True
        if name not in self.cacheable:
            return await self._call(tool, name, arguments)
        # Read-only tool: reuse a finished or in-flight identical call (parallel sub-questions often overlap).
        cache_key = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
        if cache_key in self._cache:
            self.cache_hits += 1
            return await asyncio.shield(self._cache[cache_key])
        future = asyncio.ensure_future(self._call(tool, name, arguments))
        self._cache[cache_key] = future
        text, is_error = await asyncio.shield(future)
        if is_error:
            self._cache.pop(cache_key, None)  # don't keep failures
        return text, is_error

    async def _call(self, tool: BridgedTool, name: str, arguments: dict) -> tuple[str, bool]:
        try:
            result = await self.sessions[tool.server].call_tool(tool.tool, arguments)
        except Exception as e:  # transport or validation failure
            return f"Error calling {name}: {e}", True

        parts = []
        for item in result.content:
            if getattr(item, "type", None) == "text":
                parts.append(item.text)
            else:
                parts.append(f"[{getattr(item, 'type', 'content')} omitted]")
        structured = _field(result, "structuredContent", "structured_content")
        if not parts and structured is not None:
            parts.append(json.dumps(structured, ensure_ascii=False))
        text = "\n".join(parts)
        if _field(result, "isError", "is_error"):
            return f"Error: {text}", True
        return text, False

    def clear_cache(self) -> None:
        self._cache.clear()
        self.cache_hits = 0

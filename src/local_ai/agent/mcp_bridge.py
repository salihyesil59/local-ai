"""Bridge between MCP servers and an OpenAI-compatible tool-calling model.

Every tool of every configured server is exposed to the model as
`<server>__<tool>` (e.g. `library__search`, `notebook__write_note`),
so tools with the same name on different servers never collide.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ServerConfig

SEPARATOR = "__"


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
    def __init__(self, servers: list[ServerConfig]):
        self.servers = servers
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, BridgedTool] = {}
        self._stack = AsyncExitStack()
        # Read-only tools whose results may be reused within a run (set by the agent).
        self.cacheable: set[str] = set()
        self._cache: dict[tuple[str, str], asyncio.Future] = {}
        self.cache_hits = 0

    async def __aenter__(self) -> MCPBridge:
        try:
            for server in self.servers:
                await self._connect(server)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def _connect(self, server: ServerConfig) -> None:
        if server.command:
            params = StdioServerParameters(
                command=server.command,
                args=server.args,
                env={**os.environ, **server.env},
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif server.url:
            from mcp.client.streamable_http import streamablehttp_client

            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(server.url, headers=server.headers or None)
            )
        else:
            raise ValueError(f"MCP server {server.name!r} has neither 'command' nor 'url'.")

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions[server.name] = session

        result = await session.list_tools()
        for tool in result.tools:
            name = public_tool_name(server.name, tool.name)
            self.tools[name] = BridgedTool(
                public_name=name,
                server=server.name,
                tool=tool.name,
                description=tool.description or "",
                input_schema=_field(tool, "inputSchema", "input_schema") or {"type": "object", "properties": {}},
            )

    def has(self, name: str) -> bool:
        return name in self.tools

    def openai_tools(self) -> list[dict]:
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
        ]

    async def call(self, name: str, arguments: dict | str | None) -> tuple[str, bool]:
        """Call a bridged tool. Returns (text, is_error). Never raises for
        model mistakes: errors come back as text so the model can retry."""
        tool = self.tools.get(name)
        if tool is None:
            return (
                f"Error: unknown tool {name!r}. Available tools: {', '.join(sorted(self.tools))}",
                True,
            )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return f"Error: tool arguments are not valid JSON ({e}). Send a JSON object.", True
        if not isinstance(arguments, dict):
            return "Error: tool arguments must be a JSON object.", True
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

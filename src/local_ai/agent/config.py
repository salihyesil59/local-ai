"""Configuration: LM Studio connection and the MCP server list (mcp.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MCP_CONFIG = Path("~/.lmstudio/mcp.json").expanduser()
DEFAULT_BASE_URL = "http://localhost:1234/v1"


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

    Default: LOCAL_AI_MCP_CONFIG, else LM Studio's ~/.lmstudio/mcp.json.
    """
    path = path or os.environ.get("LOCAL_AI_MCP_CONFIG")
    path = Path(path).expanduser() if path else DEFAULT_MCP_CONFIG
    if not path.is_file():
        raise FileNotFoundError(
            f"MCP config not found: {path}. Pass --mcp-config or create it from config/lmstudio/mcp.json.example."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = []
    for name, spec in data.get("mcpServers", {}).items():
        if only and name not in only:
            continue
        if spec.get("disabled"):
            continue
        servers.append(
            ServerConfig(
                name=name,
                command=spec.get("command"),
                args=list(spec.get("args", [])),
                env=dict(spec.get("env", {})),
                url=spec.get("url"),
                headers=dict(spec.get("headers", {})),
            )
        )
    return servers


@dataclass
class AgentSettings:
    base_url: str = field(default_factory=lambda: os.environ.get("LOCAL_AI_BASE_URL", DEFAULT_BASE_URL))
    api_key: str = field(default_factory=lambda: os.environ.get("LOCAL_AI_API_KEY", "lm-studio"))
    model: str | None = field(default_factory=lambda: os.environ.get("LOCAL_AI_MODEL"))
    temperature: float = 0.6
    top_p: float = 0.95
    max_steps: int = 12  # tool-calling steps per sub-question
    quick_steps: int = 6  # tool-calling steps for a quick answer
    max_tokens: int = 8192  # per completion, so a model stuck in a loop cannot run forever
    report_tokens: int = 16384  # per completion that writes or corrects the report (thinking included)
    stream: bool = False  # stream model output as "token" events (the web UI turns it on)
    max_tool_chars: int = 6000  # tool output kept in context (head + tail)
    verify_rounds: int = 1
    notebook_server: str = "notebook"
    critic: bool = True  # reviewer pass on the draft
    gap_check: bool = True  # add sub-questions for gaps after research
    learn: bool = True  # save verified facts/lessons to long-term memory
    max_gap_questions: int = 2
    parallel: int = 1  # sub-questions researched at the same time
    role_overrides: dict[str, str] = field(default_factory=dict)  # e.g. {"search": "library__search"}

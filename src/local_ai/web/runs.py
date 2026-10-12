"""A run is one chat of the web UI: a research question or a coding task, the follow-up messages sent in the
same chat, and the events they produced."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from local_ai.coder import state as coder_state
from local_ai.coder.loop import CoderAgent, Settings
from local_ai.core.llm import LLM
from local_ai.core.mcp_bridge import MCPBridge
from local_ai.core.text import trim
from local_ai.research.config import AgentSettings
from local_ai.research.loop import ResearchAgent
from local_ai.servers.export import render_markdown

CODE_SERVERS = ("library", "compute", "web")  # MCP servers a code run may use when the browser names none
ACTIVE = ("queued", "running")
# Settings a chat may change, with the range the page may ask for: name -> (type, lowest, highest).
LIMITS = {
    "temperature": (float, 0.0, 2.0),
    "max_tokens": (int, 256, 65536),  # per model reply, thinking included
    "max_turns": (int, 1, 500),  # code: model replies in one run
    "context_chars": (int, 20_000, 2_000_000),  # code: the conversation is compacted above this
    "max_steps": (int, 1, 100),  # research: tool-calling steps per sub-question
}


def clean_settings(raw) -> dict:
    """The settings of a chat as sent by the page, without what is missing, empty or out of range."""
    raw = raw if isinstance(raw, dict) else {}
    settings = {}
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        settings["model"] = raw["model"].strip()[:200]
    for key, (kind, low, high) in LIMITS.items():
        try:
            value = kind(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if low <= value <= high:
            settings[key] = value
    return settings
EARLIER_CHARS = 3000  # of the last answer shown to the agent with a follow-up message
FOLLOW_UP = "{message}\n\nEarlier in this chat the user asked:\n{question}\n\nThe answer was:\n{answer}"


@dataclass
class Run:
    id: str
    question: str
    project: str
    status: str = "queued"  # queued | running | done | stopped | failed | cancelled | interrupted
    mode: str = "research"  # research | code
    title: str = ""  # chosen by the user; empty = shortened from the question
    folder: str | None = None  # id of the chat folder it is sorted into
    pinned: bool = False  # kept at the top of the chat list
    settings: dict = field(default_factory=dict)  # model, temperature, ... chosen for this chat
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

    def last_question(self) -> str:
        """The newest message of the user in this chat."""
        return next((e["text"] for e in reversed(self.events) if e["kind"] == "user"), self.question)

    def follow_up(self, message: str) -> str:
        """A follow-up message as the task for an agent, which starts without the conversation: the earlier
        question and its answer are added."""
        answer = next((e["text"] for e in reversed(self.events) if e["kind"] in ("answer", "report")), "")
        if not answer:
            return message
        return FOLLOW_UP.format(
            message=message, question=trim(self.last_question(), 600), answer=trim(answer, EARLIER_CHARS)
        )

    def summary(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "project": self.project,
            "status": self.status,
            "mode": self.mode,
            "title": self.title,
            "folder": self.folder,
            "pinned": self.pinned,
            "settings": self.settings,
            "created": self.created,
        }


async def run_code(
    run: Run, task: str, options: dict, settings: AgentSettings, client, bridge: MCPBridge | None
) -> None:
    """Code mode: the coding agent edits a project folder and runs its tests until they pass."""
    root = Path(str(options.get("folder", ""))).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not task or task.startswith("(continue"):
        task = "Continue the existing plan and finish the task."
    run.push({"kind": "log", "text": f"Project folder: {root}"})

    extra_tools = {}
    if bridge:
        selected = options.get("servers")
        allowed = (set(selected) if selected is not None else set(CODE_SERVERS)) - {"coder"}
        extra_tools = bridge.as_tools(allowed)
    if extra_tools:
        active = sorted({bridge.tools[name].server for name in extra_tools})
        run.push({"kind": "log", "text": f"Active MCP servers: {', '.join(active)} ({len(extra_tools)} tools)"})

    llm = LLM(
        client, settings.model, max_tokens=settings.max_tokens, temperature=settings.temperature, top_p=settings.top_p
    )
    limits = Settings(**{k: run.settings[k] for k in ("max_turns", "context_chars") if k in run.settings})
    agent = CoderAgent(
        llm, root, limits, emit=lambda kind, **data: run.push({"kind": kind, **data}), extra_tools=extra_tools
    )
    run.agent = agent
    coder_state.moim_file().write_text(coder_state.IDLE, encoding="utf-8")
    outcome = await agent.run(task)
    if agent.steering:
        text = "Your last message arrived after the agent's final step and was not read. Send it again."
        run.push({"kind": "log", "level": "warning", "text": text})
    run.push({"kind": "outcome", "finished": outcome.finished, "reason": outcome.reason})
    if outcome.summary:
        run.push({"kind": "answer", "text": outcome.summary, "html": render_markdown(outcome.summary)})
    run.status = "done" if outcome.finished else "stopped"


async def run_research(
    run: Run,
    question: str,
    project: str,
    options: dict,
    settings: AgentSettings,
    client,
    bridge: MCPBridge,
    on_event: Callable[[dict], None],
) -> None:
    """Research mode: a quick answer, a full research run or the rest of an interrupted one."""
    fast = bool(options.get("fast"))
    settings.critic = settings.critic and not fast
    settings.gap_check = settings.gap_check and not fast
    settings.learn = settings.learn and not fast
    settings.parallel = max(1, min(int(options.get("parallel") or settings.parallel), 8))
    agent = ResearchAgent(client, bridge, settings, on_event=on_event)
    if options.get("quick") and not options.get("resume"):
        await agent.quick(question)
    elif options.get("resume"):
        await agent.run(project=project, resume=True)
    else:
        await agent.run(question, project=project or None)
    run.status = "done"

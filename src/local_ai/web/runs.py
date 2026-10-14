"""A run is one chat of the web UI: a research question or a coding task, the follow-up messages sent in the
same chat, and the events they produced."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from local_ai.chat import ChatAgent
from local_ai.coder import state as coder_state
from local_ai.coder.loop import CoderAgent, Settings
from local_ai.core import images as img
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
    "max_thinking_chars": (int, 1_000, 200_000),  # code: a reply that only thinks is stopped after this
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
# The faster ways of a code run: what changes when the chat has not chosen otherwise in its settings.
SPEEDS = {
    "fast": {"max_thinking_chars": 5000},
    "quick": {"max_thinking_chars": 3000, "max_turns": 25},
}
CHAT_THINKING_CHARS = 16_000  # about 5000 tokens of thinking in a chat reply
NOTEBOOK_TOOLS = ("library__search_library", "library__read_library")
NOTEBOOK_NOTE = (
    "\n\n[The user chose the notebook \"{name}\" for this message. Its files: {files}. search_library searches "
    "only these files (you need not pass scope); answer from them and say which file and page each part of the "
    "answer comes from. Pass scope=\"\" only when the user asks to search beyond the notebook.]"
)
IMAGES_ONLY = "Look at the attached image and help with what it shows."  # a message that is only images
FOLLOW_UP = "{message}\n\nEarlier in this chat the user asked:\n{question}\n\nThe answer was:\n{answer}"
STOPPED_RUN = (
    "{message}\n\nEarlier in this chat the user asked:\n{question}\n\nThat run was stopped before it had an "
    "answer. What it had found until then:\n{found}"
)


@dataclass
class Run:
    id: str
    question: str
    project: str
    status: str = "queued"  # queued | running | done | stopped | failed | cancelled | interrupted
    mode: str = "research"  # research | code | chat
    title: str = ""  # chosen by the user; empty = shortened from the question
    folder: str | None = None  # id of the chat folder it is sorted into
    pinned: bool = False  # kept at the top of the chat list
    images: list[str] = field(default_factory=list)  # names of the images sent with the first message
    notebook: str = ""  # the notebook (a folder notebooks/<name> of the library) its last message chose
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

    def last_message(self) -> list[dict]:
        """The events since the newest message of the user."""
        start = next((i for i in reversed(range(len(self.events))) if self.events[i]["kind"] == "user"), -1)
        return self.events[start + 1 :]

    def found_so_far(self) -> str:
        """What a run without an answer had gathered: the findings of its sub-questions, else what its tools
        returned. "" when the last message has its answer, or nothing was found."""
        events = self.last_message()
        if any(e["kind"] in ("answer", "report") for e in events):
            return ""
        findings = [f"## {e.get('question', '')}\n{e['text']}" for e in events if e["kind"] == "findings"]
        if findings:
            return "\n\n".join(findings)
        outputs = [e["output"] for e in events if e["kind"] == "tool_result" and not e.get("error")]
        return "\n\n-----\n\n".join(trim(o, 1200) for o in outputs)

    def resumable(self) -> str | None:
        """The research project of the last message when its run stopped before the report: it can go on."""
        if self.mode == "code" or self.status in ACTIVE:
            return None
        events = self.last_message()
        if any(e["kind"] in ("answer", "report") for e in events):
            return None
        return next((e["name"] for e in reversed(events) if e["kind"] == "project"), None)

    def follow_up(self, message: str) -> str:
        """A follow-up message as the task for an agent, which starts without the conversation: the earlier
        question comes with its answer, or, when that run was stopped, with what it had found until then."""
        found = self.found_so_far()
        if found:
            return STOPPED_RUN.format(
                message=message, question=trim(self.last_question(), 600), found=trim(found, EARLIER_CHARS)
            )
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
            "images": self.images,
            "notebook": self.notebook,
            "resumable": bool(self.loaded and self.resumable()),
            "settings": self.settings,
            "created": self.created,
        }


async def run_code(
    run: Run, task: str, options: dict, settings: AgentSettings, client, bridge: MCPBridge | None, store=None
) -> None:
    """Code mode: the coding agent edits a project folder and runs its tests until they pass. The agent of a
    chat is kept: a later message, also after a stop, goes on with its whole conversation (kept in the store,
    so also after a restart of the UI)."""
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

    speed = "quick" if options.get("quick") else "fast" if options.get("fast") else "normal"
    chosen = {**SPEEDS.get(speed, {}), **run.settings}  # what the chat set in the settings drawer wins
    if speed != "normal":
        what = "no plan; the tests run once at the end" if speed == "quick" else "no review, no project map"
        run.push({"kind": "log", "text": f"{speed.capitalize()} mode: {what}"})
    llm = LLM(
        client, settings.model, max_tokens=settings.max_tokens, temperature=settings.temperature, top_p=settings.top_p
    )
    if "max_thinking_chars" in chosen:
        llm.max_thinking_chars = chosen["max_thinking_chars"]
    limits = Settings(speed=speed, **{k: chosen[k] for k in ("max_turns", "context_chars") if k in chosen})
    agent = CoderAgent(
        llm, root, limits, emit=lambda kind, **data: run.push({"kind": kind, **data}), extra_tools=extra_tools
    )
    earlier = run.agent if isinstance(run.agent, CoderAgent) and run.agent.root == root else None
    if earlier:  # the same chat: its conversation goes on, with this message's model, limits and tools
        agent.messages, agent.interrupted = earlier.messages, earlier.interrupted
    elif store:
        saved = store.load_messages(run.id)
        agent.messages, agent.interrupted = saved.get("messages", []), bool(saved.get("interrupted"))
    if not agent.messages:  # nothing to go on with: the earlier answer of the chat comes with the message
        task = options.get("with_earlier_answer") or task
    run.agent = agent
    coder_state.moim_file().write_text(coder_state.IDLE, encoding="utf-8")
    try:
        outcome = await agent.run(task, options.get("image_paths"))
    finally:  # also when the user stops the run: the conversation so far is what the next message goes on with
        if store:
            store.save_messages(run.id, {"messages": agent.messages, "interrupted": agent.interrupted})
    if agent.steering:
        text = "Your last message arrived after the agent's final step and was not read. Send it again."
        run.push({"kind": "log", "level": "warning", "text": text})
    run.push({"kind": "outcome", "finished": outcome.finished, "reason": outcome.reason})
    if outcome.summary:
        run.push({"kind": "answer", "text": outcome.summary, "html": render_markdown(outcome.summary)})
    run.status = "done" if outcome.finished else "stopped"


async def run_chat(
    run: Run, message: str, options: dict, settings: AgentSettings, client, bridge: MCPBridge | None, store=None
) -> None:
    """Chat mode: one answer of a plain conversation. The conversation of the chat is kept like a code chat's."""
    selected = set(options.get("servers") or [])
    tools = bridge.as_tools(selected - {"coder", settings.notebook_server}) if bridge and selected else {}
    if bridge and options.get("notebook"):  # a chat with a notebook searches it, whatever else is switched on
        tools.update({n: t for n, t in bridge.as_tools({"library"}).items() if n in NOTEBOOK_TOOLS})
    if tools:
        active = sorted({bridge.tools[name].server for name in tools})
        run.push({"kind": "log", "text": f"Tools of: {', '.join(active)} ({len(tools)} tools)"})
    llm = LLM(
        client,
        settings.model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        top_p=settings.top_p,
        # longer than in code mode, but a reply that thinks in circles is still stopped (and asked to answer)
        max_thinking_chars=run.settings.get("max_thinking_chars", CHAT_THINKING_CHARS),
    )
    context = run.settings.get("context_chars", 300_000)
    agent = ChatAgent(llm, emit=lambda kind, **data: run.push({"kind": kind, **data}), tools=tools, context_chars=context)
    if isinstance(run.agent, ChatAgent):
        agent.messages = run.agent.messages
    elif store:
        agent.messages = store.load_messages(run.id).get("messages", [])
    if not agent.messages:  # nothing to go on with: the earlier answer of the chat comes with the message
        message = options.get("with_earlier_answer") or message
    run.agent = agent
    try:
        answer = await agent.reply(message, options.get("image_paths"))
    finally:  # also when the user stops it: the next message goes on with the conversation
        if store:
            store.save_messages(run.id, {"messages": agent.messages})
    run.push({"kind": "answer", "text": answer, "html": render_markdown(answer)})
    run.status = "done"


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
    paths = options.get("image_paths") or []
    if paths:  # the research agent works with text: the model writes down what the images show
        on_event({"kind": "log", "text": f"Reading the {len(paths)} attached image(s)"})
        llm = LLM(client, settings.model, max_tokens=settings.max_tokens, temperature=settings.temperature)
        text = await img.describe(llm, paths, question)
        on_event({"kind": "log", "text": f"What the image(s) show: {trim(text, 1500)}"})
        question = img.described(question, len(paths), text)
    agent = ResearchAgent(client, bridge, settings, on_event=on_event)
    if options.get("quick") and not options.get("resume"):
        await agent.quick(question)
    elif options.get("resume"):
        await agent.run(project=project, resume=True)
    else:
        await agent.run(question, project=project or None)
    run.status = "done"

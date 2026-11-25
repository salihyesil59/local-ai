"""A run is one chat of the web UI: a research question or a conversation, the follow-up messages sent in the
same chat, and the events they produced."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from local_ai.chat import ChatAgent
from local_ai.core import images as img
from local_ai.core.llm import LLM
from local_ai.core.mcp_bridge import MCPBridge
from local_ai.core.text import trim
from local_ai.research.config import AgentSettings
from local_ai.research.loop import ResearchAgent
from local_ai.servers.export import render_markdown

ACTIVE = ("queued", "running")
# Settings a chat may change, with the range the page may ask for: name -> (type, lowest, highest).
LIMITS = {
    "temperature": (float, 0.0, 2.0),
    "max_tokens": (int, 256, 65536),  # per model reply, thinking included
    "context_chars": (int, 20_000, 2_000_000),  # chat: older messages are dropped above this
    "max_thinking_chars": (int, 1_000, 200_000),  # chat: a reply that only thinks is stopped after this
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
STREAMED_THINKING = 30_000  # characters of a finished reply's thinking kept in the chat (its end)
CHAT_THINKING_CHARS = 16_000  # about 5000 tokens of thinking in a chat reply
NOTEBOOK_TOOLS = ("library__search_library", "library__read_library")
NOTEBOOK_NOTE = (
    '\n\n[The user chose the notebook "{name}" for this message. Its files: {files}. search_library searches '
    "only these files (you need not pass scope); answer from them and say which file and page each part of the "
    'answer comes from. Pass scope="" only when the user asks to search beyond the notebook.]'
)
IMAGES_ONLY = "Look at the attached image and help with what it shows."  # a message that is only images
FILES_ONLY = "Look at the attached file(s) and help with them."  # a message that is only files
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
    mode: str = "research"  # research | chat
    title: str = ""  # chosen by the user; empty = shortened from the question
    folder: str | None = None  # id of the chat folder it is sorted into
    pinned: bool = False  # kept at the top of the chat list
    images: list[str] = field(default_factory=list)  # names of the images sent with the first message
    files: list[str] = field(default_factory=list)  # names of the other files sent with it
    notebook: str = ""  # the notebook (a folder notebooks/<name> of the library) its last message chose
    # questions sent while a research ran, with their image files: researched one after the other when it is done
    queued: list[tuple[str, list[Path]]] = field(default_factory=list)
    settings: dict = field(default_factory=dict)  # model, temperature, ... chosen for this chat
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    live: dict[str, dict] = field(default_factory=dict)  # index -> text and thinking of the reply being written
    task: asyncio.Task | None = None
    agent: object | None = None  # the chat agent of a chat, whose conversation the next message goes on with
    sink: Callable[[dict], None] | None = None  # saves events to disk
    loaded: bool = True  # False for a run read from disk whose events are not loaded yet

    def push(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "token":  # the reply being written, kept so a page that opens the chat now sees it from its start
            live = self.live.setdefault(str(event.get("index")), {"text": "", "thinking": ""})
            live["thinking" if event.get("thinking") else "text"] += event["text"]
        elif kind == "stream_end":  # a finished reply stays in the chat as one event, also for later pages
            live = self.live.pop(str(event.get("index")), None)
            if live and (live["text"] or live["thinking"]):
                done = {"kind": "streamed", "index": event.get("index"), "text": live["text"],
                        "thinking": live["thinking"][-STREAMED_THINKING:]}  # fmt: skip
                self.events.append(done)
                if self.sink:
                    self.sink(done)
        else:
            self.events.append(event)
            if self.sink:
                self.sink(event)
        for q in self.listeners:
            q.put_nowait(event)

    def live_events(self) -> list[dict]:
        """The replies being written right now, as token events: what a page that opens the chat has missed."""
        events = []
        for key, live in self.live.items():
            index = None if key == "None" else int(key) if key.isdigit() else key
            for part in ("thinking", "text"):
                if live[part]:
                    events.append({"kind": "token", "index": index, "text": live[part], "thinking": part == "thinking"})
        return events

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
        if self.status in ACTIVE:
            return None
        events = self.last_message()
        if any(e["kind"] in ("answer", "report") for e in events):
            return None
        return next((e["name"] for e in reversed(events) if e["kind"] == "project"), None)

    def follow_up(self, message: str, question: str = "") -> str:
        """A follow-up message as the task for an agent, which starts without the conversation: the earlier
        question (the last one of the chat unless given) comes with its answer, or, when that run was stopped,
        with what it had found until then."""
        question = question or self.last_question()
        found = self.found_so_far()
        if found:
            return STOPPED_RUN.format(message=message, question=trim(question, 600), found=trim(found, EARLIER_CHARS))
        answer = next((e["text"] for e in reversed(self.events) if e["kind"] in ("answer", "report")), "")
        asked = next((e for e in reversed(self.last_message()) if e["kind"] == "clarify"), None)
        if asked:  # the message answers the questions asked before the research
            answer = "Before researching, these questions were asked:\n" + "\n".join(
                f"- {q}" for q in asked["questions"]
            )
        if not answer:
            return message
        return FOLLOW_UP.format(message=message, question=trim(question, 600), answer=trim(answer, EARLIER_CHARS))

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
            "files": self.files,
            "notebook": self.notebook,
            "resumable": bool(self.loaded and self.resumable()),
            "settings": self.settings,
            "created": self.created,
        }


async def run_chat(
    run: Run, message: str, options: dict, settings: AgentSettings, client, bridge: MCPBridge | None, store=None
) -> None:
    """Chat mode: one answer of a plain conversation. The conversation of the chat is kept (in the store, so also
    after a restart of the UI): a later message, also after a stop, goes on with it."""
    selected = set(options.get("servers") or [])
    tools = bridge.as_tools(selected - {settings.notebook_server}) if bridge and selected else {}
    # a chat with a notebook, or with a large attached file (put into the library), searches and reads the library,
    # whatever else is switched on
    if bridge:  # a chat can always make a PDF or Word file of an answer, when the user asks for one
        tools.update(bridge.as_tools({"documents"}))
    if bridge and (options.get("notebook") or options.get("attachment_library")):
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
        # a reply that thinks in circles is stopped (and asked to answer)
        max_thinking_chars=run.settings.get("max_thinking_chars", CHAT_THINKING_CHARS),
    )
    context = run.settings.get("context_chars", 300_000)
    agent = ChatAgent(
        llm, emit=lambda kind, **data: run.push({"kind": kind, **data}), tools=tools, context_chars=context
    )
    if isinstance(run.agent, ChatAgent):
        agent.messages = run.agent.messages
    elif store:
        agent.messages = store.load_messages(run.id).get("messages", [])
    if not agent.messages:  # nothing to go on with: the earlier answer of the chat comes with the message
        message = options.get("with_earlier_answer") or message
    run.agent = agent
    try:
        answer = await agent.reply(message, options.get("image_paths"))
        run.push({"kind": "answer", "text": answer, "html": render_markdown(answer)})
        while agent.steering:  # sent while it answered: each gets its answer in turn (shown when it arrived)
            message, shown = agent.steering.pop(0)
            run.push({"kind": "dequeued"})
            answer = await agent.reply(message, shown)
            run.push({"kind": "answer", "text": answer, "html": render_markdown(answer)})
    finally:  # also when the user stops it: the next message goes on with the conversation
        if store:
            store.save_messages(run.id, {"messages": agent.messages})
    run.status = "done"  # no await since the last check: a message sent now finds the run done and starts anew


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
    settings.critic = settings.critic and not fast and options.get("critic", True) is not False
    settings.long_report = bool(options.get("long_report"))
    settings.document = bool(options.get("document"))
    settings.short_thinking = bool(options.get("short_thinking"))
    try:
        settings.depth = max(0, min(int(options.get("depth") or 0), 3))
    except (TypeError, ValueError):
        settings.depth = 0
    # clarifying questions only for the first message of a chat; the plan can be reviewed for any new question
    settings.clarify = bool(options.get("review")) and not options.get("follow_up")
    settings.review_plan = bool(options.get("review"))
    settings.gap_check = settings.gap_check and not fast
    settings.learn = settings.learn and not fast
    settings.parallel = max(1, min(int(options.get("parallel") or settings.parallel), 8))
    # the page offers only the optional servers: the notebook (sources, notes, report) is always the research's own
    if settings.servers is not None and settings.notebook_server not in settings.servers:
        settings.servers = [*settings.servers, settings.notebook_server]
    settings.require_sources = options.get("sources", True) is not False
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

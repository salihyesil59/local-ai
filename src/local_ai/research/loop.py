"""The research loop.

recall + skills -> plan -> research each sub-question -> gap check -> write ->
critique + automatic checks -> verify -> save -> learn

Quick mode (`quick`): recall -> one tool-calling loop on the question -> number check.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from local_ai.core.llm import UNSTICK_REPEAT_PENALTY, UNSTICK_TEMPERATURE, TokenBatcher, read_stream
from local_ai.core.mcp_bridge import MCPBridge
from local_ai.core.text import extract_json, parse_inline_tool_calls, strip_think, strip_tool_calls, trim
from local_ai.servers.common import write_notebook
from local_ai.servers.export import ExportError, export_document, pdf_engine

from . import document, prompts
from .checks import cited_numbers, unknown_citations, unverified_numbers, unverified_quotes
from .config import AgentSettings
from .render import ConsoleRenderer
from .tools import ToolRoles

EventHandler = Callable[[dict], None]
SKILL_LIMITS = re.compile(r"<!-- limits: ((?:max_(?:steps|tool_chars|tokens)=\d+ ?)+)-->\n")
CACHEABLE_ROLES = (
    "search",
    "read",
    "wikipedia",
    "arxiv",
    "read_arxiv",
    "docs",
)


@dataclass
class SubQuestion:
    question: str
    search_terms: list[str] = field(default_factory=list)
    search_terms_en: list[str] = field(default_factory=list)
    needs_python: bool = False
    skill: str = ""
    findings: str | None = None

    @classmethod
    def from_json(cls, data: dict) -> SubQuestion:
        return cls(
            question=str(data["question"]),
            search_terms=[str(t) for t in data.get("search_terms", []) or []],
            search_terms_en=[str(t) for t in data.get("search_terms_en", []) or []],
            needs_python=bool(data.get("needs_python", False)),
            skill=str(data.get("skill") or ""),
        )


class ResearchAgent:
    def __init__(
        self,
        client,
        bridge: MCPBridge,
        settings: AgentSettings,
        console: Console | None = None,
        verbose: bool = False,
        on_event: EventHandler | None = None,
    ):
        self.client = client  # openai.AsyncOpenAI (or anything with the same interface)
        self.bridge = bridge
        self.settings = settings
        self.handlers: list[EventHandler] = [on_event or ConsoleRenderer(console or Console(), verbose)]
        self.python_cells: list[dict] = []  # python-role calls, for the computations notebook fallback
        self.evidence: list[str] = []  # raw tool outputs; numbers and quotes are checked against these
        self.chapters: dict[int, str] = {}  # document mode: sub-question number -> its chapter
        self.nb = settings.notebook_server
        if not bridge.has(f"{self.nb}__write_note"):
            raise RuntimeError(f"The '{self.nb}' MCP server is required (see config/lmstudio/mcp.json.example).")
        self.roles = ToolRoles.detect(list(bridge.tools), settings.role_overrides)
        self.memory = ""
        self.preferences = ""
        self.skills = ""

    # -- model + tools ---------------------------------------------------------

    async def _complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        index: int | None = None,
        max_tokens: int | None = None,
    ):
        kwargs = dict(
            model=self.settings.model,
            messages=messages,
            temperature=self.settings.temperature,
            top_p=self.settings.top_p,
            max_tokens=max_tokens or self.settings.max_tokens,
            extra_body={"top_k": self.settings.top_k},
        )
        if tools:
            kwargs["tools"] = tools
        if not self.settings.stream:
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message
        message = await self._complete_streaming(kwargs, index)
        if message.looping:  # asked once more, sampled more freely and with repeating made costly
            self._log("The model's thinking went in circles; asking it again.", "warning")
            kwargs["temperature"] = max(self.settings.temperature, UNSTICK_TEMPERATURE)
            kwargs["extra_body"] = {**kwargs["extra_body"], "repeat_penalty": UNSTICK_REPEAT_PENALTY}
            message = await self._complete_streaming(kwargs, index)
        return message

    async def _complete_streaming(self, kwargs: dict, index: int | None):
        """Stream a completion: text and reasoning go out as batched "token" events while the message (content and
        tool calls) is rebuilt from the deltas. Thinking that goes in circles is stopped at once (looping)."""
        stream = await self.client.chat.completions.create(**kwargs, stream=True)
        batch = TokenBatcher(lambda text, thinking: self.emit("token", index=index, text=text, thinking=thinking))
        reply = await read_stream(stream, batch.add, self.settings.max_thinking_chars)
        batch.flush()
        self.emit("stream_end", index=index)
        return SimpleNamespace(
            content=reply.content or None, tool_calls=reply.tool_calls or None, looping=reply.looping
        )

    async def _ask_json(self, system: str, user: str):
        """One tool-free completion parsed as JSON; None if the reply is unusable."""
        message = await self._complete([{"role": "system", "content": system}, {"role": "user", "content": user}])
        try:
            return extract_json(message.content or "")
        except ValueError:
            return None

    def _has_nb(self, tool: str) -> bool:
        return self.bridge.has(f"{self.nb}__{tool}")

    async def _notebook(self, tool: str, **args) -> str:
        text, is_error = await self.bridge.call(f"{self.nb}__{tool}", args)
        if is_error:
            raise RuntimeError(text)
        return text

    def emit(self, kind: str, **data) -> None:
        event = {"kind": kind, "time": time.time(), **data}
        for handler in self.handlers:
            handler(event)

    def _log(self, text: str, level: str = "info") -> None:
        self.emit("log", text=text, level=level)

    def _phase(self, title: str) -> None:
        self.emit("phase", title=title)

    async def _tool_loop(
        self,
        messages: list[dict],
        finalize_prompt: str,
        index: int | None = None,
        tools: list[dict] | None = None,
        max_steps: int | None = None,
        max_tokens: int | None = None,
        max_tool_chars: int | None = None,
    ) -> tuple[str, list[str]]:
        """Let the model call tools until it answers in plain text (or hits the step limit).

        Returns (answer, raw outputs of the successful tool calls)."""
        tools = self.bridge.openai_tools(self.settings.servers) if tools is None else tools
        outputs: list[str] = []
        for _ in range(max_steps or self.settings.max_steps):
            message = await self._complete(messages, tools, index=index, max_tokens=max_tokens)
            content = strip_think(message.content)
            calls = [c.model_dump() if hasattr(c, "model_dump") else c for c in (message.tool_calls or [])]
            if not calls:
                calls = parse_inline_tool_calls(message.content or "")
                content = strip_tool_calls(content)
            if not calls:
                return content, outputs

            messages.append({"role": "assistant", "content": content or None, "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or "{}"
                self.emit("tool_call", index=index, id=call["id"], name=name, arguments=args)
                output, is_error = await self.bridge.call(name, args)
                if not is_error:
                    outputs.append(output)
                    self.evidence.append(output)
                if name == self.roles.get("python"):
                    self._record_python(args, output, is_error)
                self.emit(
                    "tool_result", index=index, id=call["id"], name=name, output=trim(output, 4000), error=is_error
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": trim(output, max_tool_chars or self.settings.max_tool_chars),
                    }
                )

        messages.append({"role": "user", "content": finalize_prompt})
        message = await self._complete(messages, index=index, max_tokens=max_tokens)
        return strip_think(message.content), outputs

    def _record_python(self, args: str, output: str, is_error: bool) -> None:
        try:
            code = json.loads(args).get("code", "")
        except (json.JSONDecodeError, AttributeError):
            return
        if code:
            self.python_cells.append(
                {
                    "code": code,
                    "stdout": "" if is_error else output,
                    "results": [],
                    "error": output if is_error else None,
                    "figures_png": [],
                }
            )

    # -- phases ----------------------------------------------------------------

    async def prepare(self, question: str) -> None:
        """Load what the agent already knows and the procedures it can follow."""
        if self._has_nb("recall"):
            self.memory = await self._notebook("recall", query=question, k=8)
            prefs = [line for line in self.memory.splitlines() if "(preference)" in line]
            self.preferences = "\n".join(prefs)
            if self.memory and not self.memory.startswith("("):
                self.emit("memory", text=self.memory)
        if self._has_nb("list_skills"):
            self.skills = await self._notebook("list_skills")

    async def plan(self, question: str) -> tuple[str, list[SubQuestion]]:
        self._phase("Plan")
        data = await self._ask_json(
            prompts.PLANNER,
            prompts.PLAN_TASK.format(
                question=question, skills=self.skills or "(none)", memory=self.memory or "(nothing yet)"
            )
            + (prompts.LONG_PLAN if self.settings.long_report or self.settings.document else ""),
        )
        language, subs = "", []
        try:
            language = str(data.get("language", ""))
            subs = [SubQuestion.from_json(s) for s in data.get("sub_questions", []) if s.get("question")]
        except (AttributeError, KeyError, TypeError):
            pass
        if not subs:
            self._log("Planner reply was not usable; researching the question as a whole.", "warning")
            subs = [SubQuestion(question=question)]
        self.emit("plan", sub_questions=[{"question": s.question, "skill": s.skill} for s in subs])
        return language, subs

    def _researcher_prompt(self, project: str, sub: SubQuestion, skill_body: str) -> str:
        check_units = self.roles.get("check_units")
        unit_rule = prompts.UNIT_RULE.format(check_units=check_units) if check_units and sub.needs_python else ""
        return prompts.RESEARCHER.format(
            tool_guide=self.roles.guide(),
            add_source=f"{self.nb}__add_source",
            project=project,
            language_rules=prompts.LANGUAGE_RULES,
            unit_rule=unit_rule,
            skill=prompts.SKILL_BLOCK.format(name=sub.skill, body=skill_body) if skill_body else "",
        )

    async def research(self, project: str, question: str, subs: list[SubQuestion], start: int = 1) -> None:
        pending = [(i, sub) for i, sub in enumerate(subs, 1) if i >= start and not sub.findings]
        if self.settings.parallel > 1 and len(pending) > 1:
            self._phase(f"Research {len(pending)} sub-questions ({self.settings.parallel} in parallel)")
            semaphore = asyncio.Semaphore(self.settings.parallel)

            async def one(i: int, sub: SubQuestion) -> None:
                async with semaphore:
                    await self._research_one(project, question, subs, i, sub)

            await asyncio.gather(*(one(i, sub) for i, sub in pending))
        else:
            for i, sub in pending:
                self._phase(f"Research {i}/{len(subs)}")
                await self._research_one(project, question, subs, i, sub)

    async def _research_one(
        self, project: str, question: str, subs: list[SubQuestion], i: int, sub: SubQuestion
    ) -> None:
        self.emit("subquestion", index=i, total=len(subs), question=sub.question, skill=sub.skill)
        skill_body = ""
        if sub.skill and self._has_nb("load_skill"):
            text, is_error = await self.bridge.call(f"{self.nb}__load_skill", {"name": sub.skill})
            skill_body = "" if is_error else text
        # A skill may raise the limits for its sub-question (front matter, passed on by load_skill).
        limits = {}
        header = SKILL_LIMITS.match(skill_body)
        if header:
            limits = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", header.group(1))}
            skill_body = skill_body[header.end() :]
            self._log(f"Using skill: {sub.skill}" if skill_body else f"Unknown skill {sub.skill!r}")
        previous = "\n\n".join(
            f"Sub-question {j}: {s.question}\n{s.findings}" for j, s in enumerate(subs, 1) if s.findings
        )
        python = self.roles.get("python")
        messages = [
            {"role": "system", "content": self._researcher_prompt(project, sub, skill_body)},
            {
                "role": "user",
                "content": prompts.RESEARCH_TASK.format(
                    question=question,
                    index=i,
                    total=len(subs),
                    sub_question=sub.question,
                    terms=", ".join(sub.search_terms + sub.search_terms_en) or "(choose your own)",
                    python_hint=f"This sub-question needs {python} for its numbers.\n"
                    if sub.needs_python and python
                    else "",
                    memory=self.memory or "(nothing)",
                    previous=previous or "(none yet)",
                ),
            },
        ]
        # Fresh message list per sub-question: earlier work enters only as its summary.
        findings, outputs = await self._tool_loop(messages, prompts.FINALIZE_FINDINGS, index=i, **limits)
        sub.findings = findings
        await self._notebook(
            "write_note", project=project, title=f"finding-{i:02d}", content=f"# {sub.question}\n\n{findings}"
        )
        # Raw tool output is kept so verification still works after --resume.
        await self._notebook(
            "write_note", project=project, title=f"evidence-{i:02d}", content="\n\n-----\n\n".join(outputs)
        )
        self.emit("findings", index=i, question=sub.question, text=findings or "(empty)")
        if self.settings.document:
            await self.chapter(project, question, subs, i)

    async def gap_check(self, question: str, subs: list[SubQuestion]) -> list[SubQuestion]:
        self._phase("Gap check")
        data = await self._ask_json(
            prompts.GAP_CHECK, prompts.GAP_TASK.format(question=question, findings=self._findings(subs))
        )
        try:
            extra = [SubQuestion.from_json(s) for s in (data or {}).get("missing", []) if s.get("question")]
        except (AttributeError, KeyError, TypeError):
            extra = []
        known = {s.question.strip().lower() for s in subs}
        extra = [s for s in extra if s.question.strip().lower() not in known][: self.settings.max_gap_questions]
        self.emit("gaps", sub_questions=[s.question for s in extra])
        return extra

    @staticmethod
    def _findings(subs: list[SubQuestion]) -> str:
        return "\n\n".join(f"## {s.question}\n{s.findings}" for s in subs)

    async def write(self, question: str, subs: list[SubQuestion], sources: str) -> str:
        self._phase("Write")
        message = await self._complete(
            [
                {"role": "system", "content": prompts.WRITER},
                {
                    "role": "user",
                    "content": prompts.WRITE_TASK.format(
                        question=question,
                        preferences=self.preferences or "(none)",
                        sources=sources,
                        findings=self._findings(subs),
                    ),
                },
            ],
            max_tokens=self.settings.report_tokens,
        )
        return strip_think(message.content)

    async def outline(self, question: str, subs: list[SubQuestion]) -> list[dict]:
        """The sections of a long report: title, what it covers and the sub-questions (1-based) it is written from."""
        numbered = "\n\n".join(f"## {i}. {s.question}\n{s.findings}" for i, s in enumerate(subs, 1))
        data = await self._ask_json(prompts.OUTLINE, prompts.OUTLINE_TASK.format(question=question, findings=numbered))
        sections = []
        for s in (data or {}).get("sections", []) if isinstance(data, dict) else []:
            if not isinstance(s, dict) or not str(s.get("title") or "").strip():
                continue
            picked = [n for n in s.get("sub_questions") or [] if isinstance(n, int) and 1 <= n <= len(subs)]
            sections.append(
                {
                    "title": str(s["title"]).strip(),
                    "covers": str(s.get("covers") or ""),
                    "subs": picked or list(range(1, len(subs) + 1)),
                }
            )
        if not sections:  # no usable outline: one section per sub-question
            sections = [
                {"title": s.question.removeprefix("[gap] "), "covers": s.question, "subs": [i]}
                for i, s in enumerate(subs, 1)
            ]
        return sections[: self.settings.max_sections]

    async def write_sections(self, question: str, subs: list[SubQuestion], sources: str) -> tuple[list[dict], list[str]]:
        """A long report: an outline, then every section in its own completion (one after the other), so each
        section has the whole token budget for itself."""
        self._phase("Outline")
        sections = await self.outline(question, subs)
        outline = "\n".join(f"{i}. {s['title']}: {s['covers']}" for i, s in enumerate(sections, 1))
        self._log(f"Outline of the report:\n{outline}")
        texts = []
        for i, section in enumerate(sections, 1):
            self._phase(f"Write section {i}/{len(sections)}: {section['title']}")
            own = [subs[n - 1] for n in section["subs"]]
            texts.append(await self._write_section(question, outline, i, section, own, sources))
        return sections, texts

    async def _write_section(
        self, question: str, outline: str, index: int, section: dict, subs: list[SubQuestion], sources: str
    ) -> str:
        """One section (or chapter) in its own completion, from the findings of its sub-questions only."""
        task = prompts.SECTION_TASK.format(
            question=question,
            outline=outline,
            index=index,
            title=section["title"],
            covers=section["covers"],
            preferences=self.preferences or "(none)",
            sources=sources,
            findings=self._findings(subs),
        )
        message = await self._complete(
            [{"role": "system", "content": prompts.SECTION_WRITER}, {"role": "user", "content": task}],
            max_tokens=self.settings.report_tokens,
        )
        return self._headed(strip_think(message.content or ""), section["title"])

    async def chapter(self, project: str, question: str, subs: list[SubQuestion], i: int) -> None:
        """Document mode: the chapter of sub-question i, written (and checked) as soon as its findings are in, and
        kept as the note chapter-NN."""
        sub = subs[i - 1]
        title = sub.question.removeprefix("[gap] ")
        section = {"title": f"{i}. {title}", "covers": sub.question}
        outline = "\n".join(f"{j}. {s.question.removeprefix('[gap] ')}" for j, s in enumerate(subs, 1))
        self._phase(f"Write chapter {i}/{len(subs)}")
        sources = await self._notebook("list_sources", project=project)
        text = await self._write_section(question, outline, i, section, [sub], sources)
        text = await self._review_section(project, question, [sub], section, text, f"chapter {i}/{len(subs)}")
        self.chapters[i] = text
        await self._notebook("write_note", project=project, title=f"chapter-{i:02d}", content=text)
        self.emit("chapter", index=i, title=section["title"])

    @staticmethod
    def _headed(text: str, title: str) -> str:
        text = text.strip()
        return text if text.startswith("#") else f"## {title}\n\n{text}"

    async def critique(self, question: str, subs: list[SubQuestion], draft: str) -> list[str]:
        data = await self._ask_json(
            prompts.CRITIC, prompts.CRITIC_TASK.format(question=question, findings=self._findings(subs), draft=draft)
        )
        issues = []
        for issue in (data or {}).get("issues", []) if isinstance(data, dict) else []:
            if isinstance(issue, dict) and issue.get("detail"):
                issues.append(f"Reviewer ({issue.get('type', 'issue')}): {issue['detail']}")
        return issues

    async def _sources(self, project: str) -> list[dict]:
        return json.loads(await self._notebook("list_sources", project=project, format="json"))

    def check(self, draft: str, sources: list[dict], cited_only: bool = False) -> list[str]:
        """Problems of a draft; cited_only (a section of a long report): only the quotes of the sources it cites."""
        evidence = "\n".join(self.evidence)
        problems = []
        numbers = unverified_numbers(draft, evidence)
        if numbers:
            problems.append("Numbers not found in any tool output: " + ", ".join(numbers))
        citations = unknown_citations(draft, len(sources))
        if citations:
            problems.append("Citations that match no registered source: " + ", ".join(f"[{n}]" for n in citations))
        if cited_only:
            cited = cited_numbers(draft)
            sources = [s for s in sources if s["n"] in cited]
        quotes = unverified_quotes(sources, evidence)
        if quotes:
            problems.append("Registered quotes not found in any tool output (possibly invented): " + "; ".join(quotes))
        return problems

    async def verify(self, project: str, question: str, subs: list[SubQuestion], draft: str) -> str:
        reviewer = []
        if self.settings.critic:
            self._phase("Review")
            reviewer = await self.critique(question, subs, draft)
            self.emit("problems", source="reviewer", items=reviewer)
        draft = await self._fix(project, draft, reviewer)
        return await self._flag_remaining(project, draft)

    async def verify_sections(
        self, project: str, question: str, subs: list[SubQuestion], sections: list[dict], texts: list[str]
    ) -> str:
        """Review and fix a long report section by section: a section with problems is rewritten alone, so the
        rest keeps its length."""
        texts = list(texts)
        for i, (section, text) in enumerate(zip(sections, texts, strict=True)):
            own = [subs[n - 1] for n in section["subs"]]
            texts[i] = await self._review_section(project, question, own, section, text, f"section {i + 1}/{len(sections)}")
        return await self._flag_remaining(project, "\n\n".join(texts))

    async def _review_section(
        self, project: str, question: str, subs: list[SubQuestion], section: dict, text: str, label: str
    ) -> str:
        """The reviewer (when on) and the checks for one section; it is rewritten alone when they find problems."""
        reviewer = []
        if self.settings.critic:
            self._phase(f"Review {label}")
            note = prompts.SECTION_NOTE.format(title=section["title"], covers=section["covers"])
            reviewer = await self.critique(question + note, subs, text)
            self.emit("problems", source="reviewer", items=reviewer)
        fixed = await self._fix(project, text, reviewer, what="section", label=f" {label}")
        return self._headed(fixed, section["title"])

    async def _fix(self, project: str, draft: str, reviewer: list[str], what: str = "report", label: str = "") -> str:
        """Send the draft (a report or one section) back with its problems until the checks pass."""
        for round_ in range(self.settings.verify_rounds):
            problems = self.check(draft, await self._sources(project), cited_only=what == "section")
            problems += reviewer if round_ == 0 else []
            if not problems:
                self._log(f"Verification{label} passed.", "success")
                return draft
            self._phase(f"Verify{label} (round {round_ + 1})")
            self.emit("problems", source="checks", items=[p for p in problems if p not in reviewer])
            check_units = self.roles.get("check_units")
            messages = [
                {
                    "role": "system",
                    "content": prompts.VERIFIER.format(
                        what=what,
                        python=self.roles.get("python") or "a calculation tool",
                        units_hint=f" with {check_units}" if check_units else "",
                        list_sources=f"{self.nb}__list_sources",
                        project=project,
                        language_rules=prompts.LANGUAGE_RULES,
                    ),
                },
                {
                    "role": "user",
                    "content": prompts.VERIFY_TASK.format(what=what, problems="\n".join(problems), draft=draft),
                },
            ]
            revised, _ = await self._tool_loop(
                messages, f"Reply now with the full corrected {what} only.", max_tokens=self.settings.report_tokens
            )
            if revised:
                draft = revised
        return draft

    async def _flag_remaining(self, project: str, draft: str) -> str:
        remaining = self.check(draft, await self._sources(project))
        if remaining:
            draft += "\n\n> **Unverified:** " + " ".join(remaining)
        return draft

    async def learn(self, project: str, question: str, report: str) -> None:
        if not self._has_nb("remember"):
            return
        self._phase("Learn")
        data = await self._ask_json(prompts.LEARN, prompts.LEARN_TASK.format(question=question, report=report))
        if not isinstance(data, dict):
            return
        by_number = {s["n"]: s for s in await self._sources(project)}
        saved = 0
        for fact in data.get("facts", [])[:8]:
            if not isinstance(fact, dict) or not fact.get("text"):
                continue
            cites = [by_number[n] for n in fact.get("cites", []) if isinstance(n, int) and n in by_number]
            if not cites:
                continue  # only remember facts that are backed by a registered source
            sources = "; ".join(f"{s['title']} ({s['origin']}: {s['locator'] or s['source_id']})" for s in cites)
            await self._notebook("remember", text=fact["text"], kind="fact", sources=sources, project=project)
            saved += 1
        for lesson in data.get("lessons", [])[:3]:
            if isinstance(lesson, str) and lesson.strip():
                await self._notebook("remember", text=lesson, kind="lesson", project=project)
                saved += 1
        self._log(f"Saved {saved} memories.", "success")

    # -- entry points ----------------------------------------------------------

    async def quick(self, question: str) -> str:
        """A fast answer: one tool-calling loop on the whole question, without plan, notes, report or review.
        When sources are required (the default), an answer given without looking anything up, or with numbers
        that no tool output shows, is sent back once to be checked with the tools, and numbers still without a
        source are flagged."""
        await self.prepare(question)
        self.bridge.clear_cache()
        self.bridge.cacheable = {self.roles.get(r) for r in CACHEABLE_ROLES if self.roles.get(r)}
        self._phase("Quick answer")
        tools = [
            t
            for t in self.bridge.openai_tools(self.settings.servers)
            if not t["function"]["name"].startswith(f"{self.nb}__")
        ]
        messages = [
            {
                "role": "system",
                "content": prompts.QUICK.format(
                    tool_guide=self.roles.guide(),
                    language_rules=prompts.LANGUAGE_RULES,
                    source_rules=prompts.SOURCE_RULES if self.settings.require_sources else prompts.NO_SOURCE_RULES,
                ),
            },
            {
                "role": "user",
                "content": prompts.QUICK_TASK.format(question=question, memory=self.memory or "(nothing)"),
            },
        ]
        answer, outputs = await self._tool_loop(
            messages, prompts.FINALIZE_QUICK, tools=tools, max_steps=self.settings.quick_steps
        )
        sources = self.settings.require_sources
        numbers = unverified_numbers(answer, "\n".join([question, *self.evidence])) if sources else []
        if sources and answer and (numbers or not outputs):
            what = (
                prompts.CHECK_NUMBERS.format(numbers=", ".join(numbers)) if numbers else prompts.CHECK_NOTHING_LOOKED_UP
            )
            self._log(f"Checking the answer with the tools: {what}")
            messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": prompts.QUICK_CHECK.format(what=what)},
            ]
            checked, _ = await self._tool_loop(
                messages, prompts.FINALIZE_QUICK, tools=tools, max_steps=self.settings.quick_steps
            )
            answer = checked or answer
            numbers = unverified_numbers(answer, "\n".join([question, *self.evidence]))
        if numbers:
            answer += "\n\n> **Unverified:** Numbers not found in any tool output: " + ", ".join(numbers)
        self.emit("answer", text=answer)
        return answer

    async def run(self, question: str | None = None, project: str | None = None, resume: bool = False) -> str:
        language = ""
        subs: list[SubQuestion] = []
        if resume:
            if not project:
                raise ValueError("--resume needs --project.")
            question, language, subs = await self._load_state(project)
            self._phase("Resume")
            self._log(question)
            await self.prepare(question)
        else:
            if not question:
                raise ValueError("A question is required.")
            await self.prepare(question)
            language, subs = await self.plan(question)
            project = project or question
        project = (await self._notebook("create_project", name=project)).split(": ", 1)[-1]
        self.bridge.clear_cache()
        self.bridge.cacheable = {self.roles.get(r) for r in CACHEABLE_ROLES if self.roles.get(r)}
        python = self.roles.get("python") or ""
        reset = python.rsplit("__", 1)[0] + "__reset"
        if self.bridge.has(reset):  # fresh kernel, so the computations notebook holds only this run
            await self.bridge.call(reset, {})
        if not resume:
            await self._save_state(project, question, language, subs)
        self.emit("project", name=project)  # from here on the run can be resumed: its plan is saved

        await self.research(project, question, subs)
        if self.settings.gap_check and not any(s.question.startswith("[gap] ") for s in subs):
            extra = await self.gap_check(question, subs)
            if extra:
                for s in extra:
                    s.question = f"[gap] {s.question}"
                start = len(subs) + 1
                subs += extra
                await self._save_state(project, question, language, subs)
                await self.research(project, question, subs, start=start)

        sources = await self._notebook("list_sources", project=project)
        if self.settings.long_report:
            sections, texts = await self.write_sections(question, subs, sources)
            report = await self.verify_sections(project, question, subs, sections, texts)
        else:
            draft = await self.write(question, subs, sources)
            report = await self.verify(project, question, subs, draft)
        saved = await self._notebook("save_report", project=project, markdown=report)
        project_dir = Path(saved.rsplit(" to ", 1)[-1]).parent
        await self.export_computations(project, project_dir)
        if self.settings.document:
            await self.write_document(project, project_dir, question, language, subs, report)
        if self.settings.learn:
            await self.learn(project, question, report)
        final = await self._notebook("read_report", project=project)
        self.emit(
            "report",
            project=project,
            path=str(project_dir / "report.md"),
            text=final,
            cache_hits=self.bridge.cache_hits,
        )
        return final

    async def write_document(
        self, project: str, project_dir: Path, question: str, language: str, subs: list[SubQuestion], report: str
    ) -> None:
        """Document mode: the chapters, the report as the synthesis and the numbered sources as document.md, then
        as .docx (and .pdf when a LaTeX engine is there). A missing pandoc only costs the Word and PDF files."""
        for i, sub in enumerate(subs, 1):
            if i not in self.chapters and sub.findings:  # e.g. researched before a resume
                await self.chapter(project, question, subs, i)
        self._phase("Document")
        chapters = [self.chapters[i] for i in sorted(self.chapters)]
        text = document.build(question, language, chapters, report, await self._sources(project))
        path = project_dir / "document.md"
        path.write_text(text, encoding="utf-8")
        files = {"md": str(path)}
        for fmt in ("docx", "pdf") if pdf_engine() else ("docx",):
            try:
                files[fmt] = str(await asyncio.to_thread(export_document, project_dir, fmt))
            except ExportError as e:
                self._log(f"No {fmt.upper()} file: {e}", "warning")
                break  # the same reason (no pandoc) would stop the next format too
        self.emit("document", **files)

    async def export_computations(self, project: str, project_dir: Path) -> None:
        """Save the run's calculations as <project>/computations.ipynb."""
        exporter = next((n for n in self.bridge.tools if n.endswith("__export_notebook")), None)
        if exporter:
            text, is_error = await self.bridge.call(exporter, {"name": "computations", "folder": project})
            if not is_error and text.startswith("Notebook saved"):
                self.emit("notebook", path=text.split(": ", 1)[1].split(" (")[0])
                return
        if self.python_cells:
            path = write_notebook(project_dir / "computations.ipynb", self.python_cells, title=project)
            self.emit("notebook", path=str(path))

    async def _save_state(self, project: str, question: str, language: str, subs: list[SubQuestion]) -> None:
        state = {
            "question": question,
            "language": language,
            "sub_questions": [{k: v for k, v in asdict(s).items() if k != "findings"} for s in subs],
        }
        await self._notebook(
            "write_note", project=project, title="plan", content=json.dumps(state, ensure_ascii=False, indent=2)
        )

    async def _load_state(self, project: str) -> tuple[str, str, list[SubQuestion]]:
        state = json.loads(await self._notebook("read_note", project=project, title="plan"))
        subs = [SubQuestion.from_json(s) for s in state["sub_questions"]]
        notes = set((await self._notebook("list_notes", project=project)).splitlines())
        for i, sub in enumerate(subs, 1):
            if f"finding-{i:02d}" in notes:
                text = await self._notebook("read_note", project=project, title=f"finding-{i:02d}")
                sub.findings = text.split("\n\n", 1)[1] if "\n\n" in text else text
            if f"evidence-{i:02d}" in notes:
                self.evidence.append(await self._notebook("read_note", project=project, title=f"evidence-{i:02d}"))
            if f"chapter-{i:02d}" in notes:
                self.chapters[i] = await self._notebook("read_note", project=project, title=f"chapter-{i:02d}")
        return state["question"], state.get("language", ""), subs

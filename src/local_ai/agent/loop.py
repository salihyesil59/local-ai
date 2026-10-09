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

from local_ai.servers.common import write_notebook

from . import prompts
from .config import AgentSettings
from .context import (
    extract_json,
    parse_inline_tool_calls,
    strip_think,
    trim,
    unknown_citations,
    unverified_numbers,
    unverified_quotes,
)
from .mcp_bridge import MCPBridge
from .render import ConsoleRenderer
from .tools import ToolRoles

EventHandler = Callable[[dict], None]
STREAM_FLUSH = 0.15  # seconds between "token" events while streaming
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
        self.nb = settings.notebook_server
        if not bridge.has(f"{self.nb}__write_note"):
            raise RuntimeError(f"The '{self.nb}' MCP server is required (see config/lmstudio/mcp.json.example).")
        self.roles = ToolRoles.detect(list(bridge.tools), settings.role_overrides)
        self.memory = ""
        self.preferences = ""
        self.skills = ""

    # -- model + tools ---------------------------------------------------------

    async def _complete(self, messages: list[dict], tools: list[dict] | None = None, index: int | None = None):
        kwargs = dict(
            model=self.settings.model,
            messages=messages,
            temperature=self.settings.temperature,
            top_p=self.settings.top_p,
            max_tokens=self.settings.max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
        if self.settings.stream:
            return await self._complete_streaming(kwargs, index)
        response = await self.client.chat.completions.create(**kwargs)
        return response.choices[0].message

    async def _complete_streaming(self, kwargs: dict, index: int | None):
        """Stream a completion: text and reasoning go out as batched "token" events while the message (content and
        tool calls) is rebuilt from the deltas."""
        stream = await self.client.chat.completions.create(**kwargs, stream=True)
        content: list[str] = []
        calls: dict[int, dict] = {}
        pending = {"text": "", "thinking": ""}
        last = time.monotonic()

        def flush() -> None:
            for kind, text in pending.items():
                if text:
                    self.emit("token", index=index, text=text, thinking=kind == "thinking")
                    pending[kind] = ""

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                pending["thinking"] += reasoning
            if delta.content:
                content.append(delta.content)
                pending["text"] += delta.content
            for tc in delta.tool_calls or []:
                call = calls.setdefault(
                    tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                call["id"] = tc.id or call["id"]
                if tc.function is not None:
                    call["function"]["name"] += tc.function.name or ""
                    call["function"]["arguments"] += tc.function.arguments or ""
            if time.monotonic() - last > STREAM_FLUSH:
                flush()
                last = time.monotonic()
        flush()
        self.emit("stream_end", index=index)
        tool_calls = [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]
        return SimpleNamespace(content="".join(content) or None, tool_calls=tool_calls or None)

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
    ) -> tuple[str, list[str]]:
        """Let the model call tools until it answers in plain text (or hits the step limit).

        Returns (answer, raw outputs of the successful tool calls)."""
        tools = self.bridge.openai_tools() if tools is None else tools
        outputs: list[str] = []
        for _ in range(max_steps or self.settings.max_steps):
            message = await self._complete(messages, tools, index=index)
            content = strip_think(message.content)
            calls = [c.model_dump() if hasattr(c, "model_dump") else c for c in (message.tool_calls or [])]
            if not calls:
                calls = parse_inline_tool_calls(message.content or "")
                content = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip()
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
                    {"role": "tool", "tool_call_id": call["id"], "content": trim(output, self.settings.max_tool_chars)}
                )

        messages.append({"role": "user", "content": finalize_prompt})
        message = await self._complete(messages, index=index)
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
            ),
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
        findings, outputs = await self._tool_loop(messages, prompts.FINALIZE_FINDINGS, index=i)
        sub.findings = findings
        await self._notebook(
            "write_note", project=project, title=f"finding-{i:02d}", content=f"# {sub.question}\n\n{findings}"
        )
        # Raw tool output is kept so verification still works after --resume.
        await self._notebook(
            "write_note", project=project, title=f"evidence-{i:02d}", content="\n\n-----\n\n".join(outputs)
        )
        self.emit("findings", index=i, question=sub.question, text=findings or "(empty)")

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
            ]
        )
        return strip_think(message.content)

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

    def check(self, draft: str, sources: list[dict]) -> list[str]:
        evidence = "\n".join(self.evidence)
        problems = []
        numbers = unverified_numbers(draft, evidence)
        if numbers:
            problems.append("Numbers not found in any tool output: " + ", ".join(numbers))
        citations = unknown_citations(draft, len(sources))
        if citations:
            problems.append("Citations that match no registered source: " + ", ".join(f"[{n}]" for n in citations))
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
        for round_ in range(self.settings.verify_rounds):
            problems = self.check(draft, await self._sources(project)) + (reviewer if round_ == 0 else [])
            if not problems:
                self._log("Verification passed.", "success")
                return draft
            self._phase(f"Verify (round {round_ + 1})")
            self.emit("problems", source="checks", items=[p for p in problems if p not in reviewer])
            check_units = self.roles.get("check_units")
            messages = [
                {
                    "role": "system",
                    "content": prompts.VERIFIER.format(
                        python=self.roles.get("python") or "a calculation tool",
                        units_hint=f" with {check_units}" if check_units else "",
                        list_sources=f"{self.nb}__list_sources",
                        project=project,
                        language_rules=prompts.LANGUAGE_RULES,
                    ),
                },
                {"role": "user", "content": prompts.VERIFY_TASK.format(problems="\n".join(problems), draft=draft)},
            ]
            revised, _ = await self._tool_loop(messages, "Reply now with the full corrected report only.")
            if revised:
                draft = revised
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
        Numbers that no tool output backs are still flagged."""
        await self.prepare(question)
        self.bridge.clear_cache()
        self.bridge.cacheable = {self.roles.get(r) for r in CACHEABLE_ROLES if self.roles.get(r)}
        self._phase("Quick answer")
        tools = [t for t in self.bridge.openai_tools() if not t["function"]["name"].startswith(f"{self.nb}__")]
        messages = [
            {
                "role": "system",
                "content": prompts.QUICK.format(tool_guide=self.roles.guide(), language_rules=prompts.LANGUAGE_RULES),
            },
            {
                "role": "user",
                "content": prompts.QUICK_TASK.format(question=question, memory=self.memory or "(nothing)"),
            },
        ]
        answer, _ = await self._tool_loop(
            messages, prompts.FINALIZE_QUICK, tools=tools, max_steps=self.settings.quick_steps
        )
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
        draft = await self.write(question, subs, sources)
        report = await self.verify(project, question, subs, draft)
        saved = await self._notebook("save_report", project=project, markdown=report)
        project_dir = Path(saved.rsplit(" to ", 1)[-1]).parent
        await self.export_computations(project, project_dir)
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
        return state["question"], state.get("language", ""), subs

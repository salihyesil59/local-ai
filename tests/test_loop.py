"""End-to-end loop tests with a scripted model instead of LM Studio."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from rich.console import Console

from local_ai.core.mcp_bridge import MCPBridge, load_mcp_config
from local_ai.research.config import AgentSettings
from local_ai.research.loop import ResearchAgent

pytestmark = pytest.mark.anyio

PHASES = {
    "You are the planning step": "plan",
    "You are an offline research agent working": "research",
    "You review the progress": "gap",
    "You write the final report": "write",
    "You plan the sections": "outline",
    "You write ONE section": "section",
    "You are a strict reviewer": "critic",
    "You are the verification step": "verify",
    "You extract durable knowledge": "learn",
    "You are a research assistant giving a quick": "quick",
}


def reply(content=None, calls=()):
    tool_calls = [
        SimpleNamespace(
            model_dump=lambda n=n, a=a, i=i: {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a)},
            }
        )
        for i, (n, a) in enumerate(calls)
    ] or None
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


class ScriptedClient:
    """Returns scripted replies per phase (detected from the system prompt) and records requests."""

    def __init__(self, script: dict[str, list], delay: float = 0):
        self.script = {k: list(v) for k, v in script.items()}
        self.requests: dict[str, list] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.delay = delay
        self.active = self.max_active = 0

    async def _create(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        phase = next(p for prefix, p in PHASES.items() if system.startswith(prefix))
        if phase == "research":  # per-sub-question scripts ("research:2") for parallel runs
            m = re.search(r"Sub-question (\d+)/", kwargs["messages"][1]["content"])
            if m and f"research:{m.group(1)}" in self.script:
                phase = f"research:{m.group(1)}"
        self.requests.setdefault(phase, []).append(kwargs)
        assert self.script.get(phase), f"no scripted reply left for phase {phase!r}"
        reply_ = self.script[phase].pop(0)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        return reply_

    def left(self):
        return {k: v for k, v in self.script.items() if v}


PROJECT = "isik-hizi"
QUESTION = "Işık hızı nedir, bir saatte kaç km gider?"
REPORT = "Işık hızı 299792458 m/s'dir [1]. Bir saatte 1079252848.8 km yol alır [1]. Bir ortamda daha yavaştır."


def full_script():
    return {
        "plan": [
            reply(
                '<think>plan it</think>{"language": "tr", "sub_questions": [{"question": "Işık hızı nedir?", '
                '"search_terms": ["ışık hızı"], "search_terms_en": ["speed of light"], "needs_python": true, '
                '"skill": "fermi-estimate"}]}'
            )
        ],
        "research": [
            # sub-question 1: search, cite (one real quote, one invented), compute, answer
            reply(calls=[("library__search_library", {"query": "speed of light"})]),
            reply(
                calls=[
                    (
                        "notebook__add_source",
                        {
                            "project": PROJECT,
                            "source_id": "Speed of light",
                            "title": "Speed of light",
                            "origin": "wikipedia",
                            "quote": "c = 299792458 m/s",
                        },
                    ),
                    (
                        "notebook__add_source",
                        {
                            "project": PROJECT,
                            "source_id": "fake",
                            "title": "Made up",
                            "origin": "other",
                            "quote": "light is made of tiny cheese particles",
                        },
                    ),
                ]
            ),
            reply(calls=[("compute__python", {"code": "c = 299792458\nprint(c * 3600 / 1000)"})]),
            reply("- c = 299792458 m/s [1]\n- In one hour light travels 1079252848.8 km (Python)."),
            # gap sub-question
            reply(calls=[("library__search_library", {"query": "speed of light measurement"})]),
            reply("- Measured value: 299792458 m/s [1]."),
        ],
        "gap": [reply('{"missing": [{"question": "How is it measured?", "search_terms_en": ["measurement"]}]}')],
        # draft contains a number no tool produced (42.5)
        "write": [reply(REPORT + " Yaklaşık 42.5 kat daha hızlıdır.")],
        "critic": [reply('{"issues": [{"type": "missing", "detail": "No caveat about light in a medium."}]}')],
        "verify": [
            reply(calls=[("compute__python", {"code": "print(c * 3600 / 1000)"})]),
            reply(REPORT),
        ],
        "learn": [
            reply(
                '{"facts": [{"text": "The speed of light in vacuum is 299792458 m/s.", "cites": [1]},'
                ' {"text": "Uncited claim", "cites": []}], "lessons": ["Wikipedia gives c directly."]}'
            )
        ],
    }


async def test_full_research_run(mcp_config, tmp_path):
    client = ScriptedClient(full_script())
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test"), console=Console(quiet=True))
        report = await agent.run(QUESTION, project="Işık hızı")
        memory, _ = await bridge.call("notebook__recall", {"query": "speed of light vacuum"})

    assert not client.left(), f"unused replies: {client.left()}"

    # skill + role-based tool guide + unit rule in the researcher prompt
    system = client.requests["research"][0]["messages"][0]["content"]
    assert "fermi-estimate" in system and "Break the target into a product" in system
    assert system.index("library__search_library") < system.index("library__wikipedia")
    assert "compute__python" in system and "compute__check_units on every formula" in system

    # gap check added and researched a second sub-question
    assert "[gap] How is it measured?" in client.requests["research"][-1]["messages"][1]["content"]

    # verification got the untraceable number, the invented quote and the reviewer issue
    problems = client.requests["verify"][0]["messages"][1]["content"]
    assert "42.5" in problems and "cheese" in problems and "light in a medium" in problems
    # the invented quote can't be fixed by rewriting, so it stays flagged in the report
    assert "Unverified" in report and "cheese" in report and "42.5" not in report.split("Unverified")[0]
    assert "## References" in report and "[1] Speed of light (wikipedia)" in report

    # think blocks never go back to the model
    for requests in client.requests.values():
        assert all("<think>" not in json.dumps(r["messages"], default=str) for r in requests)

    # only the cited fact and the lesson were remembered
    assert "299792458" in memory and "Uncited" not in memory
    workspace = tmp_path / "workspace" / PROJECT
    assert (workspace / "report.md").read_text(encoding="utf-8") == report
    notebook = json.loads((workspace / "computations.ipynb").read_text(encoding="utf-8"))
    codes = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
    assert codes[0].startswith("c = 299792458") and len(codes) == 2
    assert (workspace / "notes" / "evidence-01.md").exists()
    plan = json.loads((workspace / "notes" / "plan.md").read_text(encoding="utf-8"))
    assert plan["language"] == "tr" and len(plan["sub_questions"]) == 2


async def test_memory_feeds_next_run_and_resume_keeps_evidence(mcp_config):
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(
            ScriptedClient(full_script()), bridge, AgentSettings(model="test"), console=Console(quiet=True)
        )
        await agent.run(QUESTION, project="Işık hızı")

        # Resume: research is done, no research calls; numbers verify against saved evidence,
        # only the invented quote from the first run is still reported.
        resumed = ScriptedClient(
            {"write": [reply(REPORT)], "critic": [reply('{"issues": []}')], "verify": [reply(REPORT)]}
        )
        settings = AgentSettings(model="test", learn=False)
        agent = ResearchAgent(resumed, bridge, settings, console=Console(quiet=True))
        report = await agent.run(project=PROJECT, resume=True)
        assert not resumed.left() and "research" not in resumed.requests
        problems = resumed.requests["verify"][0]["messages"][1]["content"]
        assert "cheese" in problems and "Numbers not found" not in problems
        assert "1079252848.8" in report

        # A new question gets the remembered fact in its plan prompt.
        planner = ScriptedClient({"plan": [reply("not json")], "research": [reply("- nothing")]})
        agent = ResearchAgent(
            planner,
            bridge,
            AgentSettings(model="test", gap_check=False, critic=False, learn=False),
            console=Console(quiet=True),
        )
        with pytest.raises(AssertionError):  # stops at the unscripted write phase
            await agent.run("How fast is light in vacuum?", project="second")
        assert "299792458" in planner.requests["plan"][0]["messages"][1]["content"]


async def test_parallel_research_cache_and_events(mcp_config):
    plan = reply('{"language": "en", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')
    search = ("library__search_library", {"query": "speed of light"})
    client = ScriptedClient(
        {
            "plan": [plan],
            "research:1": [reply(calls=[search]), reply("- A done")],
            "research:2": [reply(calls=[search]), reply("- B done")],
            "write": [reply("Answer.")],
        },
        delay=0.05,
    )
    events = []
    settings = AgentSettings(model="test", parallel=2, critic=False, gap_check=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("Two things?", project="par")

    assert client.max_active == 2, "both sub-questions should be researched at the same time"
    kinds = [e["kind"] for e in events]
    assert kinds.index("plan") < kinds.index("subquestion") < kinds.index("report")
    findings = {e["index"]: e["text"] for e in events if e["kind"] == "findings"}
    assert findings == {1: "- A done", 2: "- B done"}
    report = next(e for e in events if e["kind"] == "report")
    assert report["cache_hits"] == 1  # the identical wikipedia search ran once
    assert {e["index"] for e in events if e["kind"] == "tool_call"} == {1, 2}


async def test_a_long_report_is_written_and_verified_section_by_section(mcp_config):
    source = (
        "notebook__add_source",
        {"project": "long", "source_id": "Speed of light", "title": "Speed of light", "origin": "wikipedia",
         "quote": "c = 299792458 m/s"},
    )
    client = ScriptedClient(
        {
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')],
            "research": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[source]),
                reply("- c = 299792458 m/s [1]"),
                reply("- B: nothing more"),
            ],
            "outline": [
                reply(
                    '{"sections": [{"title": "Kısa cevap", "covers": "the answer", "sub_questions": [1, 2]},'
                    ' {"title": "Işık hızı", "covers": "the value of c", "sub_questions": [1]},'
                    ' {"title": "Sınırlar", "covers": "limits", "sub_questions": [9]}]}'
                )
            ],
            "section": [
                reply("## Kısa cevap\n\nIşık hızı 299792458 m/s'dir [1]."),
                reply("Değer 299792458 m/s [1]; yaklaşık 42.5 kat."),  # no heading, a number no tool shows
                reply("## Sınırlar\n\nBir ortamda daha yavaştır."),
            ],
            "critic": [reply('{"issues": []}'), reply('{"issues": []}'), reply('{"issues": []}')],
            "verify": [reply("Değer 299792458 m/s [1].")],  # only the second section is rewritten
        }
    )
    settings = AgentSettings(model="test", gap_check=False, learn=False, long_report=True)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, console=Console(quiet=True))
        report = await agent.run("Işık hızı?", project="long")

    assert not client.left(), f"unused replies: {client.left()}"
    assert "5-8 sub-questions" in client.requests["plan"][0]["messages"][1]["content"]
    sections = [r["messages"][1]["content"] for r in client.requests["section"]]
    assert "- B: nothing more" in sections[0] and "- B: nothing more" not in sections[1]
    assert "- B: nothing more" in sections[2]  # an unknown sub-question number: all findings
    assert "Section 2: Işık hızı" in sections[1] and "3. Sınırlar: limits" in sections[1]
    assert all(r["max_tokens"] == settings.report_tokens for r in client.requests["section"])
    critic = client.requests["critic"][1]["messages"][1]["content"]
    assert "one section of a longer report" in critic and "Işık hızı" in critic
    problems = client.requests["verify"][0]["messages"][1]["content"]
    assert "42.5" in problems and "Draft section" in problems and "Kısa cevap" not in problems
    body = report.split("## References")[0]
    assert body.index("## Kısa cevap") < body.index("## Işık hızı") < body.index("## Sınırlar")
    assert "42.5" not in report and "Unverified" not in report


async def test_document_mode_adds_a_chapter_after_each_step_and_the_report_at_the_end(
    mcp_config, tmp_path, monkeypatch
):
    from local_ai.research import loop

    def fake_export(project_dir, fmt):
        out = project_dir / "exports" / f"document.{fmt}"
        out.parent.mkdir(exist_ok=True)
        out.write_text((project_dir / "document.md").read_text(encoding="utf-8"), encoding="utf-8")
        return out

    monkeypatch.setattr(loop, "export_document", fake_export)
    monkeypatch.setattr(loop, "pdf_engine", lambda: None)
    source = (
        "notebook__add_source",
        {"project": "doc", "source_id": "Speed of light", "title": "Speed of light", "origin": "wikipedia",
         "quote": "c = 299792458 m/s"},
    )
    client = ScriptedClient(
        {
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')],
            "research": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[source]),
                reply("- c = 299792458 m/s [1]"),
                reply("- B: nothing more"),
            ],
            "section": [reply("## 1. A?\n\nIşık hızı 299792458 m/s [1]."), reply("B hakkında bilgi yok.")],
            "write": [reply("# Sentez\n\nIşık hızı 299792458 m/s [1].")],
            "critic": [reply('{"issues": []}')] * 3,
        }
    )
    events = []
    settings = AgentSettings(model="test", gap_check=False, learn=False, document=True)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("Işık hızı?", project="doc")

        assert not client.left(), f"unused replies: {client.left()}"
        kinds = [e["kind"] for e in events]
        # each chapter right after the findings of its step, the document after the report is saved
        found, written = ([i for i, k in enumerate(kinds) if k == kind] for kind in ("findings", "chapter"))
        assert found[0] < written[0] < found[1] < written[1] < kinds.index("document") < kinds.index("report")
        assert [e["title"] for e in events if e["kind"] == "chapter"] == ["1. A?", "2. B?"]
        chapters = [r["messages"][1]["content"] for r in client.requests["section"]]
        assert "- c = 299792458" in chapters[0] and "- B: nothing more" not in chapters[0]
        assert "- B: nothing more" in chapters[1] and "Section 2: 2. B?" in chapters[1]
        [done] = [e for e in events if e["kind"] == "document"]
        assert set(done) >= {"md", "docx"} and "pdf" not in done
        text = (tmp_path / "workspace" / "doc" / "document.md").read_text(encoding="utf-8")
        order = ["# Bölüm I", "## 1. A?", "## 2. B?", "B hakkında bilgi yok.", "# Bölüm II", "## Sentez", "# Kaynakça",
                 "[1] *Speed of light*"]
        positions = [text.index(s) for s in order]
        assert positions == sorted(positions)

        # resumed: the chapters are read back, not written again
        again = ScriptedClient({"write": [reply("# Sentez\n\nYine [1].")], "critic": [reply('{"issues": []}')]})
        agent = ResearchAgent(again, bridge, settings, console=Console(quiet=True))
        await agent.run(project="doc", resume=True)
        assert not again.left() and "section" not in again.requests
        assert "## 2. B?" in (tmp_path / "workspace" / "doc" / "document.md").read_text(encoding="utf-8")


async def test_thinking_that_goes_in_circles_is_stopped_and_asked_again(mcp_config):
    plan = '{"language": "en", "sub_questions": [{"question": "A?"}]}'

    class LoopingClient:
        def __init__(self):
            self.requests = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            first = len(self.requests) == 1
            pieces = ["Sub-question 3: Palatini identity, terms. " * 3] * 60 if first else ["Plan it."]

            def chunk(reasoning=None, content=None):
                delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
                return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)

            async def stream():
                for p in pieces:
                    yield chunk(reasoning=p)
                if not first:
                    yield chunk(content=plan)

            return stream()

    client, events = LoopingClient(), []
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test", stream=True), on_event=events.append)
        language, subs = await agent.plan("A question?")

    assert [s.question for s in subs] == ["A?"] and len(client.requests) == 2
    first, again = client.requests
    assert first["extra_body"] == {"top_k": 20} and first["temperature"] == 0.6
    assert again["temperature"] == 0.9 and again["extra_body"]["repeat_penalty"] == 1.1
    assert any(e["kind"] == "log" and "went in circles" in e["text"] for e in events)


async def test_short_thinking_stops_at_its_limit_and_asks_for_the_reply(mcp_config):
    plan = '{"language": "en", "sub_questions": [{"question": "A?"}]}'

    class LongThinkingClient:
        def __init__(self):
            self.requests = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            first = len(self.requests) == 1
            pieces = [f"Step {i}: consider term number {i * 7} of the variation. " for i in range(300)] if first else []

            def chunk(reasoning=None, content=None):
                delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
                return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)

            async def stream():
                for p in pieces:
                    yield chunk(reasoning=p)
                yield chunk(content=plan)

            return stream()

    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        normal = LongThinkingClient()  # about 17 000 characters of thinking: under the normal limit
        agent = ResearchAgent(normal, bridge, AgentSettings(model="test", stream=True), on_event=lambda e: None)
        await agent.plan("A question?")
        assert len(normal.requests) == 1

        short, events = LongThinkingClient(), []
        settings = AgentSettings(model="test", stream=True, short_thinking=True)
        agent = ResearchAgent(short, bridge, settings, on_event=events.append)
        _, subs = await agent.plan("A question?")

    assert [s.question for s in subs] == ["A?"] and len(short.requests) == 2
    nudge = short.requests[1]["messages"][-1]
    assert nudge["role"] == "user" and nudge["content"].startswith("Your last reply was cut off")
    assert "of the variation." in nudge["content"]  # where its thinking stood
    assert len(short.requests[0]["messages"]) == 2  # the conversation of the caller is not changed
    assert any(e["kind"] == "log" and "reached its limit" in e["text"] for e in events)


async def test_quick_answer(mcp_config):
    client = ScriptedClient(
        {
            "quick": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[("compute__python", {"code": "print(299792458 * 3600 / 1000)"})]),
                reply(
                    "Işık hızı 299792458 m/s (Wikipedia: Speed of light); bir saatte 1079252848.8 km. Yaklaşık 42.5 kat."
                ),
                # sent back once for the number no tool shows; the model keeps it
                reply(
                    "Işık hızı 299792458 m/s (Wikipedia: Speed of light); bir saatte 1079252848.8 km. Yaklaşık 42.5 kat."
                ),
            ]
        }
    )
    events = []
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test"), on_event=events.append)
        answer = await agent.quick(QUESTION)
        projects, _ = await bridge.call("notebook__list_projects", {})

    assert not client.left()
    tools = {t["function"]["name"] for t in client.requests["quick"][0]["tools"]}
    assert "library__search_library" in tools and not any(n.startswith("notebook__") for n in tools)
    assert answer.startswith("Işık hızı 299792458 m/s")
    check = client.requests["quick"][3]["messages"][-1]["content"]
    assert "no tool output shows: 42.5" in check and "1079252848.8" not in check
    assert "Unverified" in answer and "42.5" in answer.split("Unverified")[1]  # the made-up number is flagged
    assert "1079252848.8" not in answer.split("Unverified")[1]  # computed numbers pass
    assert [e for e in events if e["kind"] == "answer"] and "isik" not in projects  # no project, no report


async def test_a_quick_answer_from_memory_is_checked_with_the_tools(mcp_config):
    client = ScriptedClient(
        {
            "quick": [
                reply("Sıvı azot 1 atm'de 77 K'de kaynar."),  # from memory, no tool call
                reply(calls=[("compute__python", {"code": "print(273.15 - 196)"})]),
                reply("Sıvı azot 1 atm'de 77.15 K'de kaynar (Python)."),
            ]
        }
    )
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test"), on_event=lambda e: None)
        answer = await agent.quick("Sıvı azot kaç kelvinde kaynar?")
    assert not client.left()
    sent = [m["content"] for m in client.requests["quick"][1]["messages"] if m["role"] == "user"]
    assert sent[-1].startswith("Your answer states numbers that no tool output shows: 77")
    assert answer.startswith("Sıvı azot 1 atm'de 77.15 K") and "Unverified" not in answer


async def test_a_quick_answer_that_used_its_tools_is_not_sent_back(mcp_config):
    client = ScriptedClient(
        {
            "quick": [
                reply(calls=[("compute__python", {"code": "print(6 * 7)"})]),
                reply("6 × 7 = 42 (Python)."),
            ]
        }
    )
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test"), on_event=lambda e: None)
        assert await agent.quick("6 kere 7?") == "6 × 7 = 42 (Python)."
    assert not client.left() and len(client.requests["quick"]) == 2


async def test_without_required_sources_a_quick_answer_is_neither_checked_nor_flagged(mcp_config):
    client = ScriptedClient({"quick": [reply("Sıvı azot 1 atm'de yaklaşık 77 K'de kaynar.")]})
    settings = AgentSettings(model="test", require_sources=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=lambda e: None)
        answer = await agent.quick("Sıvı azot kaç kelvinde kaynar?")
    assert answer == "Sıvı azot 1 atm'de yaklaşık 77 K'de kaynar."  # no second round, no Unverified note
    system = client.requests["quick"][0]["messages"][0]["content"]
    assert "need not name sources" in system and "Name the source of each fact" not in system


class StreamingClient(ScriptedClient):
    """Streams each scripted reply as chunks: reasoning, text in pieces, a tool call split over two chunks."""

    async def _create(self, **kwargs):
        assert kwargs.pop("stream") is True
        message = (await super()._create(**kwargs)).choices[0].message

        def chunk(**delta):
            fields = {"content": None, "tool_calls": None, "reasoning_content": None, **delta}
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields))])

        chunks = [chunk(reasoning_content="Thinking about it. ")]
        text = message.content or ""
        chunks += [chunk(content=text[i : i + 5]) for i in range(0, len(text), 5)]
        for i, call in enumerate(c.model_dump() for c in message.tool_calls or []):
            args = call["function"]["arguments"]
            fn = SimpleNamespace(name=call["function"]["name"], arguments=args[:4])
            chunks.append(chunk(tool_calls=[SimpleNamespace(index=i, id=call["id"], function=fn)]))
            rest = SimpleNamespace(name=None, arguments=args[4:])
            chunks.append(chunk(tool_calls=[SimpleNamespace(index=i, id=None, function=rest)]))

        async def stream():
            for c in chunks:
                yield c

        return stream()


async def test_streaming_rebuilds_message_and_emits_tokens(mcp_config):
    client = StreamingClient(
        {
            "quick": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply("Işık hızı 299792458 m/s (speed_of_light.md)."),
            ]
        }
    )
    events = []
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test", stream=True), on_event=events.append)
        answer = await agent.quick(QUESTION)

    assert answer == "Işık hızı 299792458 m/s (speed_of_light.md)."
    calls = [e for e in events if e["kind"] == "tool_call"]
    assert [c["name"] for c in calls] == ["library__search_library"]  # rebuilt from two chunks
    assert json.loads(calls[0]["arguments"]) == {"query": "speed of light"}
    tokens = [e for e in events if e["kind"] == "token"]
    assert "".join(e["text"] for e in tokens if not e["thinking"]) == answer
    assert any(e["thinking"] for e in tokens)
    assert [e["kind"] for e in events].count("stream_end") == 2


async def test_skill_limits_apply_to_its_sub_question(mcp_config):
    library = mcp_config.parent / "library"
    (library / "paper.txt").write_text("alpha " * 500 + "omega 299792458 " * 200, encoding="utf-8")  # 6200 characters
    client = ScriptedClient(
        {
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "Özet", "skill": "paper-summary"}]}')],
            "research": [
                reply(calls=[("library__read_library", {"source": "paper.txt", "page": 1, "pages": 3})]),
                reply("- omega 299792458 (p. 2)"),
            ],
            "write": [reply("omega 299792458")],
        }
    )
    settings = AgentSettings(model="test", critic=False, gap_check=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, console=Console(quiet=True))
        await agent.run("paper.txt dosyasını özetle", project="ozet")

    first, second = client.requests["research"]
    assert (
        "Read the WHOLE paper" in first["messages"][0]["content"] and "limits:" not in first["messages"][0]["content"]
    )
    assert first["max_tokens"] == 16384
    assert (
        "characters omitted" not in second["messages"][-1]["content"]
    )  # over the default 6000, under the skill's 12000

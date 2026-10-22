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
    "You reflect on one step": "reflect",
    "You draft the answer": "draft",
    "You prepare the work": "clarify",
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
        "draft": [reply("A draft from memory.")],
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
            reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
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
        planner = ScriptedClient(
            {"draft": [reply("A draft.")], "plan": [reply("not json")], "research": [reply("- nothing")]}
        )
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
            "draft": [reply("A draft from memory.")],
            "plan": [plan],
            # each is asked once to register a source for what it found, and answers again
            "research:1": [reply(calls=[search]), reply("- A done"), reply("- A done")],
            "research:2": [reply(calls=[search]), reply("- B done"), reply("- B done")],
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
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')],
            "research": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
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
    assert "4-8 checks" in client.requests["plan"][0]["messages"][1]["content"]
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
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')],
            "research": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
                reply(calls=[source]),
                reply("- c = 299792458 m/s [1]"),
                reply("- B: nothing more"),
            ],
            "write": [reply("# Sentez\n\nIşık hızı 299792458 m/s [1].")],
            "critic": [reply('{"issues": []}')],
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
        assert "section" not in client.requests  # a chapter is its findings: no model reply spent on it
        [done] = [e for e in events if e["kind"] == "document"]
        assert set(done) >= {"md", "docx"} and "pdf" not in done
        text = (tmp_path / "workspace" / "doc" / "document.md").read_text(encoding="utf-8")
        order = ["# Bölüm I", "## 1. A?", "- c = 299792458 m/s [1]", "## 2. B?", "- B: nothing more", "# Bölüm II",
                 "## Sentez", "# Kaynakça", "[1] *Speed of light*"]
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


async def test_deepening_follows_what_a_step_opened_one_level_down(mcp_config, tmp_path):
    client = ScriptedClient(
        {
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "en", "sub_questions": [{"question": "Vary the action?"}]}')],
            "research": [reply("- δS needs δ√-g [1]"), reply("- δ√-g = -½√-g g_μν δg^μν"), reply("- δR_μν: Palatini")],
            "reflect": [
                reply(
                    '{"follow_ups": [{"question": "How does √-g vary?"}, {"question": "What is the Palatini '
                    'identity?"}, {"question": "A third one"}]}'
                )
            ],
            "write": [reply("The derivation.")],
        }
    )
    events = []
    settings = AgentSettings(model="test", depth=1, gap_check=False, critic=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("Derive the field equations", project="deep")

    assert not client.left(), f"unused replies: {client.left()}"
    assert len(client.requests["reflect"]) == 1  # the follow-ups (level 1) are not reflected on again
    reflected = client.requests["reflect"][0]["messages"][1]["content"]
    assert "1. [done] Vary the action?" in reflected and "δS needs δ√-g" in reflected
    asked = [r["messages"][1]["content"] for r in client.requests["research"]]
    assert "Sub-question 2/3: [deeper] How does √-g vary?" in asked[1] and "Palatini" in asked[2]
    assert [e["sub_questions"] for e in events if e["kind"] == "gaps"] == [
        ["[deeper] How does √-g vary?", "[deeper] What is the Palatini identity?"]  # at most 2 per step
    ]
    plan = json.loads((tmp_path / "workspace" / "deep" / "notes" / "plan.md").read_text(encoding="utf-8"))
    assert [s["level"] for s in plan["sub_questions"]] == [0, 1, 1]


async def test_clarifying_questions_and_a_plan_to_approve(mcp_config):
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        asking = ScriptedClient({"clarify": [reply('{"questions": ["Which metric signature?", "  "]}')]})
        events = []
        settings = AgentSettings(model="test", clarify=True, review_plan=True, learn=False, gap_check=False, critic=False)
        agent = ResearchAgent(asking, bridge, settings, on_event=events.append)
        assert await agent.run("Derive the field equations") == ""
        assert [e["questions"] for e in events if e["kind"] == "clarify"] == [["Which metric signature?"]]
        assert "plan" not in asking.requests  # nothing is planned before the answers

        # clear enough: the plan is made, saved and shown, and the run stops there
        planning = ScriptedClient(
            {
                "clarify": [reply('{"questions": []}')],
                "draft": [reply("A draft from memory.")],
                "plan": [reply('{"language": "en", "sub_questions": [{"question": "A?"}, {"question": "B?"}]}')],
            }
        )
        events = []
        agent = ResearchAgent(planning, bridge, settings, on_event=events.append)
        assert await agent.run("Derive (-,+,+,+)", project="review") == ""
        [shown] = [e for e in events if e["kind"] == "plan_review"]
        assert shown == {**shown, "project": "review", "sub_questions": ["A?", "B?"]} and "research" not in planning.requests

        # the user kept only B: the research goes on from the changed plan
        plan = json.loads((await bridge.call("notebook__read_note", {"project": "review", "title": "plan"}))[0])
        plan["sub_questions"] = plan["sub_questions"][1:]
        await bridge.call("notebook__write_note", {"project": "review", "title": "plan", "content": json.dumps(plan)})
        going = ScriptedClient({"research": [reply("- B done")], "write": [reply("Answer.")]})
        agent = ResearchAgent(going, bridge, settings, console=Console(quiet=True))
        assert (await agent.run(project="review", resume=True)).startswith("Answer.")  # resumed: no review again
        assert "Sub-question 1/1: B?" in going.requests["research"][0]["messages"][1]["content"]


async def test_a_source_is_registered_only_after_reading_it(mcp_config):
    def source(origin, quote):
        return ("notebook__add_source", {"project": "read", "source_id": origin, "title": origin, "origin": origin,
                                         "quote": quote})  # fmt: skip

    client = ScriptedClient(
        {
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "en", "sub_questions": [{"question": "c?"}]}')],
            "research": [
                reply(calls=[("compute__python", {"code": "print(299792458 / 1000)"})]),
                reply(calls=[source("python", "299792.458")]),  # a computation: at once
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[source("wikipedia", "c = 299792458 m/s")]),  # from a search hit only: refused
                reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
                reply(calls=[source("pdf", "c = 299792458 m/s")]),  # read first: registered
                reply("- c = 299792458 m/s [2]"),
            ],
            "write": [reply("c = 299792458 m/s [2].")],
        }
    )
    events = []
    settings = AgentSettings(model="test", gap_check=False, critic=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("What is c?", project="read")
        listed = json.loads((await bridge.call("notebook__list_sources", {"project": "read", "format": "json"}))[0])

    results = [e for e in events if e["kind"] == "tool_result" and e["name"] == "notebook__add_source"]
    assert [r["error"] for r in results] == [False, True, False] and "read the relevant part" in results[1]["output"]
    assert [s["origin"] for s in listed] == ["python", "pdf"]


async def test_what_was_read_before_the_request_to_register_counts_as_read(mcp_config):
    client = ScriptedClient(
        {
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "en", "sub_questions": [{"question": "c?"}]}')],
            "research": [
                reply(calls=[("library__read_library", {"source": "speed_of_light.md"})]),
                reply("- c = 299792458 m/s"),  # read, but registered and cited nothing: asked to register
                reply(calls=[("notebook__add_source", {"project": "again", "source_id": "speed_of_light.md",
                                                       "title": "c", "origin": "pdf", "quote": "c = 299792458"})]),
                reply("- c = 299792458 m/s [1]"),
            ],
            "write": [reply("c = 299792458 m/s [1].")],
        }
    )  # fmt: skip
    events = []
    settings = AgentSettings(model="test", gap_check=False, critic=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("What is c?", project="again")

    (result,) = [e for e in events if e["kind"] == "tool_result" and e["name"] == "notebook__add_source"]
    assert result["error"] is False  # the page read before the request still counts


def test_internet_search_and_page_reading_are_research_roles():
    from local_ai.research.tools import ToolRoles

    roles = ToolRoles.detect(["web__search", "web__fetch_content", "web__expand_link", "library__search_library"])
    assert roles.get("web_search") == "web__search" and roles.get("web_fetch") == "web__fetch_content"
    guide = roles.guide()
    assert guide.index("library__search_library") < guide.index("web__search") < guide.index("web__fetch_content")


async def test_short_thinking_leaves_the_writing_its_full_thinking(mcp_config):
    class ThinkingClient:
        def __init__(self):
            self.requests = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            self.requests.append(kwargs)

            def chunk(reasoning=None, content=None):
                delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
                return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)

            async def stream():
                for i in range(200):  # about 9 000 characters: over the short limit
                    yield chunk(reasoning=f"Section plan, point {i}: explain term {i}. ")
                yield chunk(content="## The report\n\nLong text.")

            return stream()

    client = ThinkingClient()
    settings = AgentSettings(model="test", stream=True, short_thinking=True)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=lambda e: None)
        written = await agent._complete([{"role": "user", "content": "Write."}], max_tokens=settings.report_tokens)
        assert written.content.startswith("## The report") and len(client.requests) == 1
        step = await agent._complete([{"role": "user", "content": "Next step."}])  # a step: cut and asked again
        assert step.content and len(client.requests) == 3


async def test_an_empty_report_or_section_is_replaced_by_the_findings(mcp_config, tmp_path, monkeypatch):
    from local_ai.research import loop

    monkeypatch.setattr(loop, "export_document", lambda project_dir, fmt: project_dir / "document.md")
    monkeypatch.setattr(loop, "pdf_engine", lambda: None)
    def script(**more):
        return {
            "draft": [reply("The draft from memory: δ√-g = ½√-g g^μν δg_μν.")],
            "plan": [reply('{"language": "en", "sub_questions": [{"question": "Vary √-g?"}]}')],
            "research": [reply("- # Checked\n- δ√-g = -½√-g g_μν δg^μν")],
            **more,
        }

    settings = AgentSettings(model="test", gap_check=False, critic=False, learn=False, document=True)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        events = []
        agent = ResearchAgent(ScriptedClient(script(write=[reply("")])), bridge, settings, on_event=events.append)
        report = await agent.run("Derive", project="empty")  # nothing came back from the writer
        body = report.split("## References")[0]
        assert "The draft from memory" in body and "δ√-g = -½√-g g_μν δg^μν" in body
        chapter = (tmp_path / "workspace" / "empty" / "notes" / "chapter-01.md").read_text(encoding="utf-8")
        assert chapter.startswith("## 1. Vary √-g?") and "δ√-g = -½√-g" in chapter  # the chapter is the findings
        assert any("draft and the findings stand in" in e["text"] for e in events if e["kind"] == "log")

        events = []
        long = AgentSettings(model="test", gap_check=False, critic=False, learn=False, long_report=True)
        outline = reply('{"sections": [{"title": "Variation", "covers": "δ√-g", "sub_questions": [1]}]}')
        client = ScriptedClient(script(outline=[outline], section=[reply("## Variation")]))  # only its heading
        agent = ResearchAgent(client, bridge, long, on_event=events.append)
        report = await agent.run("Derive", project="empty-section")
        assert "## Variation" in report and "δ√-g = -½√-g g_μν δg^μν" in report
        assert "Draft answer (from memory)" in client.requests["section"][0]["messages"][1]["content"]
        assert any("came back empty" in e["text"] for e in events if e["kind"] == "log")


async def test_a_call_written_as_text_at_the_step_limit_is_no_answer(mcp_config):
    client = ScriptedClient(
        {
            "research": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply("<tool_call>\n<function=notebook__add_source>\n<parameter=title>\nX\n</parameter>\n</function>"
                      "\n</tool_call>\n- c = 299792458 m/s"),  # fmt: skip
            ]
        }
    )
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, AgentSettings(model="test"), console=Console(quiet=True))
        messages = [{"role": "system", "content": "You are an offline research agent working"}, {"role": "user", "content": "c?"}]
        answer, _ = await agent._tool_loop(messages, "Finish.", max_steps=1)
    assert answer == "- c = 299792458 m/s"


async def test_a_research_drafts_the_answer_then_checks_and_revises_it(mcp_config, tmp_path):
    draft = "## Cevap\n\nIşık hızı yaklaşık 3e8 m/s'dir (emin değilim: tam değer)."
    client = ScriptedClient(
        {
            "draft": [reply(draft)],
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "c tam olarak kaç m/s?"}]}')],
            "research": [reply("- c = 299792458 m/s (tam, tanım gereği)")],
            "write": [reply("## Cevap\n\nIşık hızının değeri tanım gereği tamdır.")],
        }
    )
    events = []
    settings = AgentSettings(model="test", gap_check=False, critic=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, on_event=events.append)
        await agent.run("Işık hızı nedir?", project="draft")

        kinds = [e["kind"] for e in events]
        assert kinds.index("draft") < kinds.index("plan") < kinds.index("findings") < kinds.index("report")
        assert next(e for e in events if e["kind"] == "draft")["text"] == draft  # shown at once, as a chat would
        assert client.requests["draft"][0]["max_tokens"] == settings.report_tokens and "tools" not in client.requests["draft"][0]
        assert draft in client.requests["plan"][0]["messages"][1]["content"]  # the checks are of the draft
        written = client.requests["write"][0]["messages"][1]["content"]
        assert draft in written and "c = 299792458 m/s" in written  # the report revises the draft with the findings
        note = (tmp_path / "workspace" / "draft" / "notes" / "draft.md").read_text(encoding="utf-8")
        assert draft in note

        # resumed: the draft is read back for the report, not written again
        again = ScriptedClient({"write": [reply("## Cevap\n\nYine.")]})
        agent = ResearchAgent(again, bridge, settings, console=Console(quiet=True))
        await agent.run(project="draft", resume=True)
        assert "draft" not in again.requests and draft in again.requests["write"][0]["messages"][1]["content"]


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
            "draft": [reply("A draft from memory.")],
            "plan": [reply('{"language": "tr", "sub_questions": [{"question": "Özet", "skill": "paper-summary"}]}')],
            "research": [
                reply(calls=[("library__read_library", {"source": "paper.txt", "page": 1, "pages": 3})]),
                reply("- omega 299792458 (p. 2)"),
                reply("- omega 299792458 (p. 2)"),  # asked to register what it read; it does not
            ],
            "write": [reply("omega 299792458")],
        }
    )
    settings = AgentSettings(model="test", critic=False, gap_check=False, learn=False)
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        agent = ResearchAgent(client, bridge, settings, console=Console(quiet=True))
        await agent.run("paper.txt dosyasını özetle", project="ozet")

    first, second, reminded = client.requests["research"]
    assert "you registered no source" in reminded["messages"][-1]["content"]
    assert (
        "Read the WHOLE paper" in first["messages"][0]["content"] and "limits:" not in first["messages"][0]["content"]
    )
    assert first["max_tokens"] == 16384
    assert (
        "characters omitted" not in second["messages"][-1]["content"]
    )  # over the default 6000, under the skill's 12000

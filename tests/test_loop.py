"""End-to-end loop tests with a scripted model instead of LM Studio."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from rich.console import Console

from local_ai.agent.config import AgentSettings, load_mcp_config
from local_ai.agent.loop import ResearchAgent
from local_ai.agent.mcp_bridge import MCPBridge

pytestmark = pytest.mark.anyio

PHASES = {
    "You are the planning step": "plan",
    "You are an offline research agent working": "research",
    "You review the progress": "gap",
    "You write the final report": "write",
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


async def test_quick_answer(mcp_config):
    client = ScriptedClient(
        {
            "quick": [
                reply(calls=[("library__search_library", {"query": "speed of light"})]),
                reply(calls=[("compute__python", {"code": "print(299792458 * 3600 / 1000)"})]),
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
    assert "Unverified" in answer and "42.5" in answer.split("Unverified")[1]  # the made-up number is flagged
    assert "1079252848.8" not in answer.split("Unverified")[1]  # computed numbers pass
    assert [e for e in events if e["kind"] == "answer"] and "isik" not in projects  # no project, no report


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

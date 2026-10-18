"""Settings of the research agent: the LM Studio connection and the limits of a run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from local_ai.core.llm import DEFAULT_BASE_URL


@dataclass
class AgentSettings:
    base_url: str = field(default_factory=lambda: os.environ.get("LOCAL_AI_BASE_URL", DEFAULT_BASE_URL))
    api_key: str = field(default_factory=lambda: os.environ.get("LOCAL_AI_API_KEY", "lm-studio"))
    model: str | None = field(default_factory=lambda: os.environ.get("LOCAL_AI_MODEL"))
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 20  # what Qwen recommends for thinking; also keeps the thinking from drifting into loops
    # Thinking that repeats itself is stopped at once and asked again; this many characters of thinking without any
    # answer stop a reply too (above what a report needs, so in practice only loops stop it).
    max_thinking_chars: int = 60_000
    # short thinking: a reply may think this much (about 1700 tokens), then it is asked to answer from where it stood
    short_thinking: bool = False
    short_thinking_chars: int = 6_000
    max_steps: int = 12  # tool-calling steps per sub-question
    quick_steps: int = 6  # tool-calling steps for a quick answer
    # A quick answer names the source of each fact; one from memory, or with numbers no tool shows, is checked once
    # and what stays without a source is flagged. Off: the tools are used as needed, sources are not required.
    require_sources: bool = True
    max_tokens: int = 8192  # per completion, so a model stuck in a loop cannot run forever
    report_tokens: int = 16384  # per completion that writes or corrects the report (thinking included)
    stream: bool = False  # stream model output as "token" events (the web UI turns it on)
    max_tool_chars: int = 6000  # tool output kept in context (head + tail)
    verify_rounds: int = 1
    notebook_server: str = "notebook"
    critic: bool = True  # reviewer pass on the draft
    long_report: bool = False  # an outline, then each section written (and verified) in its own completion
    max_sections: int = 10
    # a chapter per sub-question right after its research, then the report as the synthesis: a .docx (and .pdf)
    document: bool = False
    gap_check: bool = True  # add sub-questions for gaps after research
    learn: bool = True  # save verified facts/lessons to long-term memory
    max_gap_questions: int = 2
    # deepening: after each sub-question the findings are reflected on and follow-up questions added (at most 2 per
    # step, 3 per level, down to this many levels; 0 = off)
    depth: int = 0
    clarify: bool = False  # ask the user a few questions before planning
    review_plan: bool = False  # stop after the plan, so the user can change it and start the research
    read_before_cite: bool = True  # a source is registered only after a part of it was read (not from a search hit)
    parallel: int = 1  # sub-questions researched at the same time
    servers: list[str] | None = None  # subset of servers active for this run (None = all)
    role_overrides: dict[str, str] = field(default_factory=dict)  # e.g. {"search": "library__search"}

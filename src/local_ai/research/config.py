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
    parallel: int = 1  # sub-questions researched at the same time
    servers: list[str] | None = None  # subset of servers active for this run (None = all)
    role_overrides: dict[str, str] = field(default_factory=dict)  # e.g. {"search": "library__search"}

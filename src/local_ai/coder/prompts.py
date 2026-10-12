"""Prompts of the coding agent."""

from __future__ import annotations

from pathlib import Path

from local_ai.coder.workflow import INSTRUCTIONS

SYSTEM = (
    "You are a coding agent. Your project folder is {root}. Reply to the user in Turkish; keep code, identifiers "
    "and comments in English.\n\n"
    "{instructions}\n\n"
    "Tools: plan, verify, status, load_skill, read_file, write_file, edit_file, list_files, search, repo_map, "
    "outline. Paths are relative to "
    "the project folder. There is no shell: tests run only through verify. This program checks your work for you: "
    "if you stop before verify reports ALL DONE, or edit several files without calling verify, it runs verify and "
    "shows you the result. Your reply is cut off when it gets too long, thinking included, so keep your reasoning "
    "short and act.{skills}"
)
SKILLS = (
    "\n\nSkills are short procedures for kinds of tasks. Before plan, call load_skill with the name of the one that "
    "fits and follow it (the first plan is refused until a skill has been read); when a skill is given with the "
    "task, follow that one. In the review, load edge-cases.\n{list}"
)

GIVEN_SKILL = (
    "\n\nThe skill for this kind of task is {name}. It is given here, you need not load it; follow it from "
    "the first step on:\n{body}"
)
LARGE_PROJECT = (
    "\n\nThis is a large project: it does not fit into your context, so work as the existing-code skill says:\n"
    "{skill}\n\n{map}"
)
CUT_OFF = (
    "Your last reply was cut off because it was too long (thinking included). Do not work it out in your head "
    "again: act now with a tool call. To find a bug, call verify and read what fails, or read the code again; "
    "then change one thing and call verify."
)
CUT_OFF_CALL = (
    "Your last reply was cut off in the middle of a tool call, so it was not run. Do not think it through again. "
    "Write less in one call: split a large file into several smaller files or write it in parts with edit_file, "
    "then act now."
)
AUTO_VERIFY = "You made {edits} edits without calling verify, so verify was run for you:\n{result}"
STOPPED = "You stopped, but the task is not finished. verify was run for you:\n{result}"
NEEDS_PLAN = "You stopped, but there is no plan yet. Call plan first, then implement the task."
EARLIER_PLAN = (
    "\n\nAn earlier task in {root} is finished and its plan has been put aside. This is a new task. Look at the code, "
    "then call plan for it (a small change is a plan with one step) and work through it until verify reports "
    "ALL DONE."
)
NEEDS_NEW_PLAN = (
    "There is no plan for this task yet (the plan of the earlier task is finished and put aside). Call plan for "
    "the user's request now, then implement it."
)
SUMMARY = "Verification reports ALL DONE. Reply now, without tools, with a short summary in Turkish of what changed."
STEER = "The user sent this message while you were working. Follow it from now on:\n{message}"
COMPACTED = (
    "Earlier messages were dropped to keep the context short. The work is on disk. Current plan:\n{status}\n\n"
    "Files:\n{files}\n{notes}\nContinue with the current step; read a file again if you need its content."
)
NOTES = "\nYour notes on the dropped part of the conversation:\n{note}\n"
COMPACT_SYSTEM = "You write progress notes for a coding agent whose conversation is about to be cut short."
COMPACT_REQUEST = (
    "Below is the start of a coding session that will be dropped from the context. Write notes for yourself, at most "
    "250 words, in English: what the code does and in which files, decisions made and why, what failed and what you "
    "learned from it, and what to do next. Facts only; no tools.\n\n{transcript}"
)


def first_message(
    task: str,
    root: Path,
    status: str | None,
    skill: dict | None = None,
    earlier_plan: bool = False,
    overview: str = "",
) -> str:
    """The task for the model. status: an unfinished plan to continue; earlier_plan: a finished one is on disk;
    overview: for a large project, how to work in it and its map."""
    if status is None:
        given = GIVEN_SKILL.format(name=skill["name"], body=skill["body"]) if skill else ""
        return task + given + overview + (EARLIER_PLAN.format(root=root) if earlier_plan else "")
    return (
        f"{task}{overview}\n\nA plan from an earlier run exists in {root}; continue it instead of starting over:\n"
        f"{status}"
    )


def system_prompt(root: Path, skills: str = "", extra_tools: list[str] | None = None) -> str:
    prompt = SYSTEM.format(root=root, instructions=INSTRUCTIONS, skills=SKILLS.format(list=skills) if skills else "")
    if extra_tools:
        prompt += (
            f"\n\nAdditional tools available: {', '.join(sorted(extra_tools))}. "
            "Use them when helpful:\n"
            "- library__read_arxiv: read arXiv papers by URL or ID (supports page ranges, or query='algorithm' to find sections)\n"
            "- library__read_pdf: read a PDF file on this computer by its absolute path (same page and query arguments)\n"
            "- library__search_arxiv: search arXiv for papers\n"
            "- compute__python: run Python code to verify numerical results or formulas\n"
            "- web__search: look up documentation, APIs or error messages\n"
            "- library__search_library / library__read_library: read offline library and documentation."
        )
    return prompt


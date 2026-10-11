"""The agent's tools, called in-process: the same functions the MCP server offers, with the project folder fixed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import server as sv
from local_ai.coder import skills as sk
from local_ai.coder import state as st

NO_PLAN_YET = "Error: there is no plan yet. Call plan first; then edit files."


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


STR = {"type": "string"}
STRS = {"type": "array", "items": {"type": "string"}}
INT = {"type": "integer"}

EDIT_TOOLS = ("write_file", "edit_file")


def coder_tools(root: Path, require_skill: bool = True) -> dict[str, Tool]:
    seen = {"skill": not require_skill}  # a skill has been read in this run

    def plan(goal: str, steps: list[str], criteria: list[str], verify_command: str = "") -> str:
        steps = [steps] if isinstance(steps, str) else steps
        criteria = [criteria] if isinstance(criteria, str) else criteria
        skills = sk.load_skills()
        if not seen["skill"] and skills and st.load(root) is None:
            # The model does not load skills by itself: the program picks the one that fits and hands it over.
            name = sk.suggest(skills, " ".join([goal, *steps, *criteria]))
            seen["skill"] = True
            return (
                f"Error: the plan was not saved. Before planning, read the skill for this kind of task ({name}) "
                f"and plan according to it, then call plan again:\n\n{skills[name]['body']}"
            )
        return sv.make_plan(root, goal, steps, criteria, verify_command)

    def verify(note: str = "") -> str:
        return sv.run_verify(root, note)

    def status() -> str:
        return sv.show_status(root)

    def write_file(path: str, content: str) -> str:
        if st.load(root) is None:
            return NO_PLAN_YET
        return fl.write_file(root, path, content)

    def edit_file(path: str, old: str, new: str) -> str:
        if st.load(root) is None:
            return NO_PLAN_YET
        return fl.edit_file(root, path, old, new)

    def load_skill(name: str) -> str:
        found = sk.load_skills()
        skill = found.get(name.strip())
        seen["skill"] = True
        if skill is None:
            return f"Error: unknown skill {name!r}. Skills: {', '.join(found)}"
        return f"Skill {skill['name']}:\n{skill['body']}"

    def read_file(path: str, start: int = 1, end: int = 0) -> str:
        return fl.read_file(root, path, start, end)

    def list_files(path: str = ".") -> str:
        return fl.list_files(root, path)

    tools = [
        Tool(
            "plan",
            "Write the plan for the task. Call this first; calling it again replaces the plan. steps: small changes to "
            "the code, each including its tests (never a step like 'write the tests'). criteria: every requirement and "
            "example of the request with its exact expected result, one per item. Leave verify_command empty.",
            _schema(
                {"goal": STR, "steps": STRS, "criteria": STRS, "verify_command": STR}, ["goal", "steps", "criteria"]
            ),
            plan,
        ),
        Tool(
            "verify",
            "Run the tests for the current step. PASSED gives the next step (or the review); FAILED gives the failure "
            "to fix. note: one line about what you did, or in the review what you added.",
            _schema({"note": STR}, []),
            verify,
        ),
        Tool(
            "load_skill",
            "Load the procedure of a skill (see the skill list in the instructions) and follow it.",
            _schema({"name": STR}, ["name"]),
            load_skill,
        ),
        Tool("status", "Show the plan, the current step and the last verification result.", _schema({}, []), status),
        Tool(
            "read_file",
            "Read a text file of the project. start/end are 1-based line numbers (end 0 = to the end).",
            _schema({"path": STR, "start": INT, "end": INT}, ["path"]),
            read_file,
        ),
        Tool(
            "write_file",
            "Create a file or replace it completely; missing folders are created. Prefer edit_file for changes.",
            _schema({"path": STR, "content": STR}, ["path", "content"]),
            write_file,
        ),
        Tool(
            "edit_file",
            "Replace one exact piece of text in a file. old must appear exactly once: copy it from read_file.",
            _schema({"path": STR, "old": STR, "new": STR}, ["path", "old", "new"]),
            edit_file,
        ),
        Tool(
            "list_files",
            "List the files under a folder of the project (caches and virtual environments are skipped).",
            _schema({"path": STR}, []),
            list_files,
        ),
    ]
    return {t.name: t for t in tools}

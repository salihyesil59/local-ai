"""The coding agent's skills: short procedures for kinds of coding tasks (format: local_ai.core.skills).

Built-in skills are in the package's skills folder. Put your own in ~/.local-coder/skills (LOCAL_CODER_HOME) as
<name>.md; a skill with the same name replaces the built-in one.
"""

from __future__ import annotations

from pathlib import Path

from local_ai.coder import state as st
from local_ai.core import skills as core

BUILTIN = Path(__file__).parent / "skills"


def load_skills(dirs: list[Path] | None = None) -> dict[str, dict]:
    return core.load_skills(dirs or [BUILTIN, st.home() / "skills"])


def suggest(skills: dict[str, dict], text: str) -> str:
    """The skill whose keywords occur most often in the text (the task); new-feature when none does."""
    text = text.lower()
    scores = {name: sum(k in text for k in skill["keywords"]) for name, skill in skills.items() if skill["keywords"]}
    best = max(scores, key=scores.get, default=None)
    if best and scores[best] > 0:
        return best
    return "new-feature" if "new-feature" in skills else next(iter(skills))


def skill_list(skills: dict[str, dict]) -> str:
    """One line per skill, for the system prompt."""
    return "\n".join(f"- {s['name']}: {s['description']} (use when: {s['when_to_use']})" for s in skills.values())

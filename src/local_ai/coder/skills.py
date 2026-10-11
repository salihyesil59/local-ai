"""Skills: short procedures for kinds of coding tasks, stored as Markdown files with front matter.

---
name: bug-fix
description: Fix a reported bug at its cause, with a test that fails before and passes after
when_to_use: the user reports wrong behaviour ...
---
<step-by-step instructions>

Built-in skills are in the package's skills folder. Put your own in ~/.local-coder/skills (LOCAL_CODER_HOME) as
<name>.md; a skill with the same name replaces the built-in one. Same format as local-ai's skills; the
optional front matter line `keywords: a, b, c` is what suggest() matches against the task.
"""

from __future__ import annotations

import re
from pathlib import Path

from local_ai.coder import state as st

BUILTIN = Path(__file__).parent / "skills"
FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_skill(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    match = FRONT_MATTER.match(text)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip('"')
        body = match.group(2)
    return {
        "name": meta.get("name") or path.stem,
        "description": meta.get("description", ""),
        "when_to_use": meta.get("when_to_use", ""),
        "keywords": [k.strip().lower() for k in meta.get("keywords", "").split(",") if k.strip()],
        "body": body.strip(),
    }


def load_skills(dirs: list[Path] | None = None) -> dict[str, dict]:
    """Later folders override earlier ones, so users can replace built-in skills."""
    skills: dict[str, dict] = {}
    for folder in dirs or [BUILTIN, st.home() / "skills"]:
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                skill = parse_skill(path)
                skills[skill["name"]] = skill
    return skills


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

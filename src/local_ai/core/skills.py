"""Skills: short procedures stored as Markdown files with front matter.

---
name: fermi-estimate
description: Order-of-magnitude estimate when exact data is missing
when_to_use: "how many / roughly how much" questions
---
<step-by-step instructions>

Optional front matter lines:
  keywords: a, b, c            what the coding agent matches against the task to suggest a skill
  max_steps, max_tool_chars, max_tokens
                               more room than the research agent's defaults, for the sub-question that uses the skill
"""

from __future__ import annotations

import re
from pathlib import Path

LIMITS = ("max_steps", "max_tool_chars", "max_tokens")
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
        "category": "code" if "coder" in path.parts else "research",
        "keywords": [k.strip().lower() for k in meta.get("keywords", "").split(",") if k.strip()],
        "limits": {k: int(meta[k]) for k in LIMITS if meta.get(k, "").isdigit()},
        "body": body.strip(),
        "path": str(path),
    }


def load_skills(dirs: list[Path]) -> dict[str, dict]:
    """Later folders override earlier ones, so users can replace built-in skills."""
    skills: dict[str, dict] = {}
    for folder in dirs:
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                skill = parse_skill(path)
                skills[skill["name"]] = skill
    return skills

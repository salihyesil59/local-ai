"""Skills: reusable research procedures stored as Markdown files with front matter.

---
name: fermi-estimate
description: Order-of-magnitude estimate when exact data is missing
when_to_use: "how many / roughly how much" questions
---
<step-by-step instructions>
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from local_ai.servers.common import BUILTIN_SKILLS, workspace_root

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def skill_dirs() -> list[Path]:
    dirs = [BUILTIN_SKILLS, workspace_root() / "skills"]
    dirs += [Path(p).expanduser() for p in os.environ.get("LOCAL_AI_SKILLS", "").split(os.pathsep) if p]
    return dirs


def parse_skill(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    m = FRONT_MATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip('"')
        body = m.group(2)
    return {
        "name": meta.get("name") or path.stem,
        "description": meta.get("description", ""),
        "when_to_use": meta.get("when_to_use", ""),
        "body": body.strip(),
        "path": str(path),
    }


def load_skills(dirs: list[Path] | None = None) -> dict[str, dict]:
    """Later directories override earlier ones, so users can replace built-in skills."""
    skills: dict[str, dict] = {}
    for d in dirs or skill_dirs():
        if d.is_dir():
            for path in sorted(d.glob("*.md")):
                skill = parse_skill(path)
                skills[skill["name"]] = skill
    return skills

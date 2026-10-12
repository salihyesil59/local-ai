"""The research agent's skills: reusable research procedures (format: local_ai.core.skills).

Built-in skills are in the package's skills folders. Put your own in <workspace>/skills or a folder listed in
LOCAL_AI_SKILLS as <name>.md; a skill with the same name replaces the built-in one.
"""

from __future__ import annotations

import os
from pathlib import Path

from local_ai.core import skills as core
from local_ai.servers.common import BUILTIN_SKILLS, workspace_root


def skill_dirs() -> list[Path]:
    coder_skills = BUILTIN_SKILLS.parent / "coder" / "skills"
    dirs = [BUILTIN_SKILLS]
    if coder_skills.is_dir():
        dirs.append(coder_skills)
    dirs.append(workspace_root() / "skills")
    dirs += [Path(p).expanduser() for p in os.environ.get("LOCAL_AI_SKILLS", "").split(os.pathsep) if p]
    return dirs


def load_skills(dirs: list[Path] | None = None) -> dict[str, dict]:
    return core.load_skills(dirs or skill_dirs())

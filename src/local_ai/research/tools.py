"""Map the tools that happen to be available to the roles the agent knows about.

The agent never hard-codes server names: whichever MCP servers are configured,
roles are detected from tool names (first match in preference order wins), and
prompts are rendered from what was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

# role -> tool name patterns, most preferred first
ROLE_PATTERNS: dict[str, list[str]] = {
    "python": ["compute__python", "*__run_python", "*__python"],
    "check_units": ["*__check_units"],
    "solve": ["*__solve"],
    "search": ["*__search_library", "*__semantic_search"],
    "read": ["*__read_library", "*__read_chunk"],
    "wikipedia": ["*__wikipedia"],
    "arxiv": ["*__search_arxiv"],
    "read_arxiv": ["*__read_arxiv"],
    "docs": ["*__docs"],
}

ROLE_GUIDE: dict[str, str] = {
    "search": "passages from the user's library and offline arXiv abstracts (sources=zim also searches the offline "
    "Wikipedia/docs archives); pass English key terms in english_query. Use this first.",
    "read": "read a search hit with its neighbours (chunk id) or pages of a library file before citing it.",
    "wikipedia": "a Wikipedia article for definitions and background (offline archive first); pass section=... for "
    "one part of a long article.",
    "arxiv": "research papers and recent results (online).",
    "read_arxiv": "read pages of an arXiv paper found by the arXiv search.",
    "docs": "offline programming documentation (Python, numpy, pandas, ...); query full names like pathlib.Path.glob.",
    "python": "ANY calculation, unit conversion, numeric estimate, formula check, table or plot. Write the code, "
    "run it, quote the number from its output. Never compute in your head. On errors fix and rerun.",
    "check_units": "dimensional analysis of every formula you derive or use (expected_unit gives OK/MISMATCH).",
    "solve": "symbolic algebra (solve/simplify equations) instead of doing algebra by hand.",
}


@dataclass
class ToolRoles:
    roles: dict[str, str]  # role -> public tool name

    @classmethod
    def detect(cls, tool_names: list[str], overrides: dict[str, str] | None = None) -> ToolRoles:
        roles: dict[str, str] = {}
        for role, patterns in ROLE_PATTERNS.items():
            for pattern in patterns:
                match = next((n for n in sorted(tool_names) if fnmatch(n, pattern)), None)
                if match:
                    roles[role] = match
                    break
        for role, name in (overrides or {}).items():
            if name in tool_names:
                roles[role] = name
        return cls(roles)

    def get(self, role: str) -> str | None:
        return self.roles.get(role)

    def guide(self) -> str:
        """Bullet list of available research tools, in the order they should be tried."""
        lines = [f"- {self.roles[r]}: {ROLE_GUIDE[r]}" for r in ROLE_GUIDE if r in self.roles]
        return "\n".join(lines) or "- (no research tools configured)"

"""Notebook MCP server: a local research workspace for the agent.

Gives the model persistent memory outside its context window:
projects, Markdown notes, a tracked list of sources (for citations),
a final report with an automatically appended bibliography, long-term memory
shared across projects (facts, lessons, preferences) and skills (reusable
research procedures from skills/*.md).

Everything lives under one root directory (LOCAL_AI_WORKSPACE, default ~/local-ai-workspace).
Paths outside that root are rejected.

Run:  local-ai-notebook
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from local_ai.servers.common import FastMCP, slugify, workspace_root
from local_ai.servers.export import ExportError, export
from local_ai.servers.notebook.memory import Memory, format_memory
from local_ai.servers.notebook.skills import load_skills

ORIGINS = ("wikipedia", "arxiv", "zim", "pdf", "python", "other")


class Notebook:
    """File-backed workspace. One directory per project:

    <root>/<project>/notes/<note>.md
    <root>/<project>/sources.json
    <root>/<project>/report.md
    """

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root).expanduser().resolve() if root else workspace_root()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------------

    def _project_dir(self, project: str, create: bool = False) -> Path:
        path = (self.root / slugify(project)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Project path escapes the workspace: {project!r}")
        if create:
            (path / "notes").mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise ValueError(f"Unknown project {project!r}. Call create_project first.")
        return path

    def _note_path(self, project: str, title: str) -> Path:
        return self._project_dir(project) / "notes" / f"{slugify(title)}.md"

    # -- projects --------------------------------------------------------------

    def create_project(self, name: str) -> str:
        path = self._project_dir(name, create=True)
        return path.name

    def list_projects(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    # -- notes -----------------------------------------------------------------

    def write_note(self, project: str, title: str, content: str) -> str:
        path = self._note_path(project, title)
        path.write_text(content, encoding="utf-8")
        return path.stem

    def append_note(self, project: str, title: str, content: str) -> str:
        path = self._note_path(project, title)
        with path.open("a", encoding="utf-8") as f:
            if path.stat().st_size:
                f.write("\n\n")
            f.write(content)
        return path.stem

    def read_note(self, project: str, title: str) -> str:
        path = self._note_path(project, title)
        if not path.is_file():
            raise ValueError(f"No note {title!r} in project {project!r}.")
        return path.read_text(encoding="utf-8")

    def list_notes(self, project: str) -> list[str]:
        notes_dir = self._project_dir(project) / "notes"
        return sorted(p.stem for p in notes_dir.glob("*.md"))

    # -- sources ---------------------------------------------------------------

    def _sources_path(self, project: str) -> Path:
        return self._project_dir(project) / "sources.json"

    def list_sources(self, project: str) -> list[dict]:
        path = self._sources_path(project)
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def add_source(
        self,
        project: str,
        source_id: str,
        title: str,
        origin: str,
        locator: str = "",
        quote: str = "",
        authors: str = "",
        year: str = "",
        doi: str = "",
        citekey: str = "",
    ) -> int:
        """Register a source and return its citation number. Re-adding the same
        source_id returns the existing number (and keeps any new quote / metadata)."""
        meta = {"authors": authors, "year": str(year or ""), "doi": doi, "citekey": citekey}
        origin = origin.lower().strip()
        if origin not in ORIGINS:
            origin = "other"
        sources = self.list_sources(project)
        for src in sources:
            if src["source_id"] == source_id:
                changed = False
                if quote and quote not in src["quotes"]:
                    src["quotes"].append(quote)
                    changed = True
                for key, value in meta.items():
                    if value and not src.get(key):
                        src[key] = value
                        changed = True
                if changed:
                    self._save_sources(project, sources)
                return src["n"]
        n = len(sources) + 1
        sources.append(
            {
                "n": n,
                "source_id": source_id,
                "title": title,
                "origin": origin,
                "locator": locator,
                "quotes": [quote] if quote else [],
                **{k: v for k, v in meta.items() if v},
            }
        )
        self._save_sources(project, sources)
        return n

    def _save_sources(self, project: str, sources: list[dict]) -> None:
        self._sources_path(project).write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- report ----------------------------------------------------------------

    def bibliography(self, project: str) -> str:
        lines = []
        for src in self.list_sources(project):
            who = f"{src['authors']} ({src.get('year') or 'n.d.'}). " if src.get("authors") else ""
            entry = f"[{src['n']}] {who}{src['title']} ({src['origin']}"
            if src["locator"]:
                entry += f": {src['locator']}"
            entry += ")"
            if src.get("doi"):
                entry += f" doi:{src['doi']}"
            lines.append(entry)
        return "\n".join(lines)

    def read_report(self, project: str) -> str:
        path = self._project_dir(project) / "report.md"
        if not path.is_file():
            raise ValueError(f"No report saved yet for project {project!r}.")
        return path.read_text(encoding="utf-8")

    def save_report(self, project: str, markdown: str) -> str:
        """Write report.md, replacing any References section the model wrote
        with one generated from the tracked sources."""
        body = re.split(r"\n#{1,6}\s*(References|Kaynakça|Kaynaklar)\s*\n", markdown)[0].rstrip()
        bib = self.bibliography(project)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        report = f"{body}\n\n## References\n\n{bib or '_No sources recorded._'}\n\n<!-- generated {stamp} -->\n"
        path = self._project_dir(project) / "report.md"
        path.write_text(report, encoding="utf-8")
        return report


def build_server(notebook: Notebook | None = None, memory: Memory | None = None, skill_dirs=None) -> FastMCP:
    nb = notebook or Notebook()
    mem = memory or Memory(nb.root / "memory.sqlite")
    mcp = FastMCP(
        "notebook",
        instructions=(
            "Research workspace. Start with recall (what you already know) and list_skills "
            "(procedures for derivations, estimates, literature reviews...). Create a project per "
            "research question, keep findings in notes, register every source you rely on with "
            "add_source and cite it as [n] using the number it returns, save the final answer with "
            "save_report, and remember verified facts and lessons for next time. "
            "Inputs may be in any language (e.g. Turkish or English)."
        ),
    )

    @mcp.tool()
    def create_project(name: str) -> str:
        """Create (or reopen) a research project. Returns the project id to use in other calls."""
        return f"Project ready: {nb.create_project(name)}"

    @mcp.tool()
    def list_projects() -> str:
        """List existing research projects."""
        return "\n".join(nb.list_projects()) or "(no projects)"

    @mcp.tool()
    def write_note(project: str, title: str, content: str) -> str:
        """Create or overwrite a Markdown note in a project (e.g. findings for one sub-question)."""
        return f"Saved note: {nb.write_note(project, title, content)}"

    @mcp.tool()
    def append_note(project: str, title: str, content: str) -> str:
        """Append text to a note, creating it if needed."""
        return f"Appended to note: {nb.append_note(project, title, content)}"

    @mcp.tool()
    def read_note(project: str, title: str) -> str:
        """Read a note back."""
        return nb.read_note(project, title)

    @mcp.tool()
    def list_notes(project: str) -> str:
        """List the notes in a project."""
        return "\n".join(nb.list_notes(project)) or "(no notes)"

    @mcp.tool()
    def add_source(
        project: str,
        source_id: str,
        title: str,
        origin: str,
        locator: str = "",
        quote: str = "",
        authors: str = "",
        year: str = "",
        doi: str = "",
        citekey: str = "",
    ) -> str:
        """Register a source you used and get its citation number [n].

        source_id: stable id (arXiv id, Wikipedia title, file path...).
        origin: one of wikipedia, arxiv, zim, pdf, python, other.
        locator: where in the source (URL, page, section).
        quote: a short EXACT passage from the tool output that supports your claim.
        authors, year, doi, citekey: copy them from the search hit when shown (for a correct bibliography).
        """
        n = nb.add_source(project, source_id, title, origin, locator, quote, authors, year, doi, citekey)
        return f"Cite this source as [{n}]."

    @mcp.tool()
    def list_sources(project: str, format: str = "text") -> str:
        """List registered sources with their citation numbers. format: "text" or "json" (includes quotes)."""
        sources = nb.list_sources(project)
        if format == "json":
            return json.dumps(sources, ensure_ascii=False)
        if not sources:
            return "(no sources)"
        return "\n".join(f"[{s['n']}] {s['title']} ({s['origin']}: {s['locator']})" for s in sources)

    @mcp.tool()
    def save_report(project: str, markdown: str) -> str:
        """Save the final report. A References section is generated from the registered sources."""
        nb.save_report(project, markdown)
        return f"Report saved to {nb._project_dir(project) / 'report.md'}"

    @mcp.tool()
    def read_report(project: str) -> str:
        """Read the saved report (with its References section)."""
        return nb.read_report(project)

    @mcp.tool()
    def export_report(project: str, format: str = "html") -> str:
        """Export the saved report. format: html (self-contained, figures embedded), md, bib (BibTeX),
        tex, docx or pdf (these three need pandoc; pdf also a LaTeX engine)."""
        try:
            return f"Exported: {export(nb._project_dir(project), format)}"
        except ExportError as e:
            return f"Error: {e}"

    # -- long-term memory --------------------------------------------------------

    @mcp.tool()
    def remember(text: str, kind: str = "fact", sources: str = "", tags: str = "", project: str = "") -> str:
        """Save something worth knowing in future research sessions.

        kind: "fact" (a verified finding; give its sources), "lesson" (what worked or failed while
        researching), or "preference" (how the user wants answers, e.g. SI units, Turkish).
        sources: where a fact comes from (titles / ids / pages), so it stays citable later.
        """
        return f"Remembered as m{mem.remember(text, kind, sources, tags, project)}."

    @mcp.tool()
    def recall(query: str, k: int = 5, kind: str = "") -> str:
        """Search long-term memory (facts, lessons, preferences) from earlier research.

        Facts found here still need their source re-checked before being cited as new evidence.
        """
        found = mem.recall(query, max(1, min(k, 20)), kind)
        prefs = [p for p in mem.preferences() if p["id"] not in {m["id"] for m in found}]
        lines = [format_memory(m) for m in found + prefs]
        return "\n".join(lines) or "(nothing relevant in memory)"

    @mcp.tool()
    def forget(memory_id: str) -> str:
        """Delete a memory that turned out wrong or outdated (id like "m12")."""
        ok = mem.forget(int(str(memory_id).lstrip("mM")))
        return "Forgotten." if ok else "No such memory."

    # -- skills ------------------------------------------------------------------

    @mcp.tool()
    def list_skills() -> str:
        """List available research skills (step-by-step procedures). Load one with load_skill."""
        skills = load_skills(skill_dirs)
        if not skills:
            return "(no skills installed)"
        return "\n".join(
            f"- {s['name']}: {s['description']}" + (f" (use when: {s['when_to_use']})" if s["when_to_use"] else "")
            for s in skills.values()
        )

    @mcp.tool()
    def load_skill(name: str) -> str:
        """Load the full instructions of a skill and follow them for the current task."""
        skills = load_skills(skill_dirs)
        if name not in skills:
            return f"Error: unknown skill {name!r}. Available: {', '.join(skills) or 'none'}"
        return skills[name]["body"]

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()

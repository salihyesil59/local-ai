"""Coder MCP server: the plan → implement → verify → fix → review loop for a coding agent (Goose, LM Studio).

Tools:
  plan(goal, steps, criteria, verify_command, project)   write the plan; returns the first step
  verify(note, timeout)                                  run the tests; next step on success, else the failure
  status()                                               plan and progress, to resume after a compaction or retry
  read_file, write_file, edit_file, list_files           only with --files, for clients without file tools

Not a sandbox: the verify command runs as your user in the project folder.

Run:  local-coder [--files]
"""

from __future__ import annotations

from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import state as st
from local_ai.coder import workflow as wf
from local_ai.servers.common import FastMCP

FILES_INSTRUCTIONS = (
    "\nFile tools (read_file, write_file, edit_file, list_files) work inside the project folder; paths are "
    "relative to it. There is no shell: tests run only through verify. Always pass the project argument (absolute "
    "folder path) to plan, since this server does not know which folder you work in."
)


def build_server(files: bool = False) -> FastMCP:
    mcp = FastMCP("coder", instructions=wf.INSTRUCTIONS + (FILES_INSTRUCTIONS if files else ""))
    root = st.default_root()  # replaced when plan is called with a project folder

    @mcp.tool()
    def plan(goal: str, steps: list[str], criteria: list[str], verify_command: str = "", project: str = "") -> str:
        """Write the plan for a coding task. Call this before editing code; calling it again replaces the plan.

        goal: what the user wants, in one or two sentences.
        steps: ordered small changes to the code, e.g.
          ["Add parse_date() for ISO dates", "Reject invalid dates with ValueError", ...].
          Every step includes its tests. Do not add steps like "write the tests" or "run the tests".
        criteria: the acceptance criteria, one per item: every requirement and every example of the user's
          request with its exact expected result, e.g. ["parse_date('2024-01-31') == date(2024, 1, 31)", ...].
          They are checked against the tests in the review at the end.
        verify_command: leave empty; the project's test command is detected (pytest, npm test, cargo test,
          go test). Set it only when the project needs another command that must exit 0 (build, linter, ...).
        project: absolute path of the project folder; leave empty for the current working directory.
        """
        nonlocal root
        if project.strip():
            root = Path(project.strip()).expanduser().resolve()
        return wf.make_plan(root, goal, steps, criteria, verify_command)

    @mcp.tool()
    def verify(note: str = "", timeout: float = 300) -> str:
        """Run the verify command for the current step. Call it after every change.

        PASSED marks the step done and returns the next one, or the review after the last step.
        FAILED returns the failure to fix.
        note: one line about what you did in this step; in the review, what you added. timeout: seconds, at most 1800.
        """
        return wf.run_verify(root, note, max(5.0, min(float(timeout), 1800.0)))

    @mcp.tool()
    def status() -> str:
        """Show the plan: goal, steps, criteria, the current step and the last verification result."""
        return wf.show_status(root)

    if files:

        @mcp.tool()
        def read_file(path: str, start: int = 1, end: int = 0) -> str:
            """Read a text file of the project. start/end are 1-based line numbers (end 0 = to the end)."""
            return fl.read_file(root, path, start, end)

        @mcp.tool()
        def write_file(path: str, content: str) -> str:
            """Create a file or replace it completely; missing folders are created. Prefer edit_file for changes."""
            return fl.write_file(root, path, content)

        @mcp.tool()
        def edit_file(path: str, old: str, new: str) -> str:
            """Replace one exact piece of text in a file. old must appear exactly once: copy it from read_file."""
            return fl.edit_file(root, path, old, new)

        @mcp.tool()
        def list_files(path: str = ".") -> str:
            """List the files under a folder of the project (caches and virtual environments are skipped)."""
            return fl.list_files(root, path)

    return mcp


def main(files: bool = False) -> None:
    # A new session starts without a task; plan and verify write the reminder again.
    st.moim_file().write_text(st.IDLE, encoding="utf-8")
    build_server(files).run()


if __name__ == "__main__":
    main()

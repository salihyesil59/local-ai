"""Terminal rendering of agent events (the web UI renders the same events in the browser)."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

STYLES = {"info": "dim", "warning": "yellow", "success": "green", "error": "red"}


class ConsoleRenderer:
    def __init__(self, console: Console, verbose: bool = False):
        self.console = console
        self.verbose = verbose

    def _print(self, text: str, style: str = "") -> None:
        self.console.print(text, style=style, markup=False, highlight=False)

    def __call__(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "phase":
            self.console.rule(event["title"])
        elif kind == "plan":
            for i, s in enumerate(event["sub_questions"], 1):
                self._print(f"  {i}. {s['question']}" + (f"  [skill: {s['skill']}]" if s.get("skill") else ""))
        elif kind == "subquestion":
            self._print(f"[{event['index']}/{event['total']}] {event['question']}")
        elif kind == "gaps":
            for q in event["sub_questions"]:
                self._print(f"  + {q}")
            if not event["sub_questions"] and self.verbose:
                self._print("No gaps found.", "green")
        elif kind == "problems":
            for item in event["items"]:
                self._print(f"  • {item}", "yellow")
            if not event["items"] and self.verbose:
                self._print(f"No issues from {event['source']}.", "green")
        elif kind == "notebook":
            self._print(f"Computations notebook: {event['path']}", "dim")
        elif not self.verbose:
            return
        elif kind == "log":
            self._print(event["text"], STYLES.get(event.get("level", "info"), "dim"))
        elif kind == "memory":
            self._print(f"Memory:\n{event['text']}", "dim")
        elif kind == "tool_call":
            args = event["arguments"]
            prefix = f"[{event['index']}] " if event.get("index") else ""
            self._print(f"{prefix}→ {event['name']} {args if len(args) < 300 else args[:300] + '…'}", "cyan")
        elif kind == "tool_result":
            out = event["output"]
            self._print(f"← {out if len(out) < 400 else out[:400] + '…'}", "red" if event["error"] else "dim")
        elif kind == "findings":
            self.console.print(Panel(Markdown(event["text"]), title=f"Findings {event['index']}"))

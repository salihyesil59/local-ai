"""The coding loop, run by this program.

The model only decides what to write next. The program decides when the work is checked and when it is over:
  - a reply that is cut off by max_tokens (a model stuck thinking) gets a nudge to act, not a longer wait;
  - a reply without tool calls while the task is not finished makes the program run verify and show the result;
  - several edits in a row without verify also make the program run it;
  - the task ends only when the state on disk is finished (verify reported ALL DONE) or a limit is hit;
  - a long conversation is compacted: the plan and the files are on disk, so old messages can go;
  - a message the user sends while the agent works (steer) is shown to the model before its next reply.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import prompts
from local_ai.coder import skills as sk
from local_ai.coder import state as st
from local_ai.coder import workflow as wf
from local_ai.coder.tools import EDIT_TOOLS, coder_tools
from local_ai.core.llm import LLM, Reply, TokenBatcher
from local_ai.core.text import parse_inline_tool_calls, size, strip_think, trim
from local_ai.core.tools import Tool, parse_arguments

Emit = Callable[..., None]
# Tools that read a paper or a book (the last part of an MCP tool name): their pages are kept whole, the
# formulas in the middle of a page must not be cut out.
READ_TOOLS = ("read_arxiv", "read_pdf", "read_library")
TRANSCRIPT_CHARS = 24_000  # of the dropped messages shown to the model that writes the notes


@dataclass
class Settings:
    max_turns: int = 80  # model replies in one run
    max_tool_chars: int = 6000  # tool output kept in context (head + tail)
    max_read_chars: int = 12_000  # ... of a tool that reads a paper (two pages)
    edits_before_verify: int = 4  # edits in a row before the program runs verify itself
    max_nudges: int = 6  # cut-off or early-stop replies in a row before giving up
    # Compact above this: about 90k tokens, so a whole paper stays in the conversation. The context length of
    # the model in LM Studio must be larger (131072 fits); lower this for a smaller one.
    context_chars: int = 300_000
    keep_messages: int = 8  # newest messages kept by a compaction
    require_skill: bool = True  # the first plan is refused once, with the fitting skill attached
    notes_tokens: int = 4096  # the model writes notes on the dropped part (thinking included); 0 = no notes


def describe_error(error: Exception) -> str:
    """One readable line for an exception; servers often answer with a whole HTML page."""
    text = " ".join(re.sub(r"<[^>]+>", " ", str(error)).split())
    return f"{type(error).__name__}: {text[:200]}" if text else type(error).__name__


@dataclass
class Outcome:
    finished: bool
    reason: str
    summary: str = ""


class CoderAgent:
    def __init__(
        self,
        llm: LLM,
        root: Path,
        settings: Settings | None = None,
        emit: Emit | None = None,
        extra_tools: dict[str, Tool] | None = None,
    ):
        self.llm = llm
        self.root = root
        self.settings = settings or Settings()
        self.emit = emit or (lambda kind, **data: None)
        self.tools: dict[str, Tool] = coder_tools(root, self.settings.require_skill)
        if extra_tools:
            self.tools.update(extra_tools)
        self.compact_requested = False
        self.steering: list[str] = []  # messages of the user that the model has not seen yet

    # -- helpers ---------------------------------------------------------------

    async def call_tool(self, call: dict) -> str:
        name = call["function"]["name"]
        raw = call["function"].get("arguments") or "{}"
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}. Available tools: {', '.join(self.tools)}"
        args, problem = parse_arguments(raw)
        self.emit("tool_call", id=call["id"], name=name, arguments=raw)
        if problem:
            self.emit("tool_result", id=call["id"], name=name, output=problem, error=True)
            return problem
        try:
            if asyncio.iscoroutinefunction(tool.fn):
                output = await tool.fn(**args)
            else:
                output = await asyncio.to_thread(tool.fn, **args)
        except TypeError as e:
            output = f"Error: wrong arguments for {name}: {e}"
        except Exception as e:
            output = f"Error: {e}"
        self.emit("tool_result", id=call["id"], name=name, output=output, error=output.startswith("Error"))
        self.emit_state()
        return output

    @staticmethod
    def bad_arguments(call: dict) -> str | None:
        """Why the tool call's arguments cannot be used, or None."""
        return parse_arguments(call["function"].get("arguments") or "{}")[1]

    async def verify(self) -> str:
        result = await asyncio.to_thread(wf.run_verify, self.root)
        self.emit("auto_verify", output=result)
        self.emit_state()
        return result

    async def complete(self, messages: list[dict], tools: list[dict] | None) -> Reply:
        """One model reply; its text and thinking go out as batched "token" events (the format of local-ai's UI)."""
        batch = TokenBatcher(lambda text, thinking: self.emit("token", text=text, thinking=thinking))
        try:
            return await self.llm.complete(messages, tools, batch.add)
        finally:
            batch.flush()
            self.emit("stream_end")

    def emit_state(self) -> None:
        """The plan and its progress, for a UI to show."""
        state = st.load(self.root)
        if state is None:
            return
        self.emit(
            "state",
            goal=state["goal"],
            steps=state["steps"],
            current=st.current(state),
            criteria=state["criteria"],
            review=state["review"],
            last=st.last_line(state),
            finished=st.finished(state),
        )

    def request_compact(self) -> None:
        """Compact before the next model reply (the UI's Compact button)."""
        self.compact_requested = True

    def steer(self, message: str) -> None:
        """A message of the user for the running task; the model reads it before its next reply."""
        self.steering.append(message)

    @staticmethod
    def transcript(messages: list[dict]) -> str:
        """The dropped messages as short text for the model that writes the notes (the newest part is kept)."""
        lines = []
        for m in messages:
            text = (m.get("content") or "").strip()
            calls = "; ".join(
                f"{c['function']['name']}({c['function']['arguments'][:200]})" for c in m.get("tool_calls") or []
            )
            lines.append(f"{m['role']}: {trim(text, 600)} {calls}".strip())
        return "\n".join(lines)[-TRANSCRIPT_CHARS:]

    async def notes(self, dropped: list[dict]) -> str:
        """The model's own notes on the part that is dropped; "" when it fails (the plan and the files remain)."""
        request = prompts.COMPACT_REQUEST.format(transcript=self.transcript(dropped))
        messages = [
            {"role": "system", "content": prompts.COMPACT_SYSTEM},
            {"role": "user", "content": request},
        ]
        try:
            reply = await self.llm.complete(messages, None, max_tokens=self.settings.notes_tokens)
        except Exception as e:  # a failed summary must not stop the work
            self.emit("log", text=f"Could not write notes for the compaction: {e}")
            return ""
        return "" if reply.finish_reason == "length" else strip_think(reply.content)

    async def compact(self, messages: list[dict], manual: bool = False) -> list[dict]:
        """Replace the middle of the conversation by the plan, the file list and the model's notes; the plan and
        the files are on disk."""
        start = max(2, len(messages) - self.settings.keep_messages)
        while start > 2 and messages[start]["role"] == "tool":  # keep a tool result with its assistant call
            start -= 1
        if start <= 2:
            self.emit("compact", dropped=0, noted=False, manual=manual)
            return messages
        note = await self.notes(messages[2:start]) if self.settings.notes_tokens else ""
        state = st.load(self.root)
        status = st.render(self.root, state) if state else "(no plan yet)"
        summary = prompts.COMPACTED.format(
            status=status, files=fl.list_files(self.root), notes=prompts.NOTES.format(note=note) if note else ""
        )
        self.emit("compact", dropped=start - 2, noted=bool(note), manual=manual)
        return [*messages[:2], {"role": "user", "content": summary}, *messages[start:]]

    def emit_turn(self, turn: int, reply: Reply, context: int) -> None:
        """What a reply cost; context is the size of the conversation it was given, in characters."""
        self.emit(
            "turn",
            turn=turn,
            max_turns=self.settings.max_turns,
            context=context,
            context_limit=self.settings.context_chars,
            prompt_tokens=reply.prompt_tokens,
            seconds=reply.seconds,
            tokens=reply.completion_tokens,
            thinking_chars=reply.reasoning_chars,
            finish=reply.finish_reason,
            tool_calls=[c["function"]["name"] for c in reply.tool_calls],
        )

    # -- the loop --------------------------------------------------------------

    async def run(self, task: str) -> Outcome:
        state = st.load(self.root)
        status = st.render(self.root, state) if state and not st.finished(state) else None
        base_tools = coder_tools(self.root, False)
        extra_names = [name for name in self.tools if name not in base_tools]
        messages = [
            {"role": "system", "content": prompts.system_prompt(self.root, sk.skill_list(sk.load_skills()), extra_names)},
            {"role": "user", "content": prompts.first_message(task, self.root, status)},
        ]
        specs = [t.spec() for t in self.tools.values()]
        edits = nudges = 0
        wrap_up = False

        for turn in range(1, self.settings.max_turns + 1):
            if self.compact_requested or size(messages) > self.settings.context_chars:
                manual, self.compact_requested = self.compact_requested, False
                messages = await self.compact(messages, manual)
            while self.steering and not wrap_up:  # in the wrap-up the model has no tools to act on it
                messages.append({"role": "user", "content": prompts.STEER.format(message=self.steering.pop(0))})
                nudges = 0
            context = size(messages)
            try:
                reply = await self.complete(messages, None if wrap_up else specs)
            except Exception as e:  # the server (LM Studio) failed; the plan and the files stay on disk
                return Outcome(False, f"the model server failed: {describe_error(e)}")
            self.emit_turn(turn, reply, context)
            calls = reply.tool_calls or parse_inline_tool_calls(reply.content)
            text = strip_think(reply.content)

            if wrap_up:
                return Outcome(True, "ALL DONE", text)

            if reply.finish_reason == "length":
                # Cut off, in the thinking or in the middle of a tool call. Nothing of it is kept or run: a half
                # written call has broken JSON, which the server's chat template cannot take back in the history.
                nudges += 1
                if nudges > self.settings.max_nudges:
                    return Outcome(False, "the model keeps running out of tokens without acting")
                messages.append({"role": "user", "content": prompts.CUT_OFF_CALL if calls else prompts.CUT_OFF})
                continue

            if calls:
                nudges = 0
                # arguments that are not valid JSON must not stay in the history either
                kept = [
                    {**c, "function": {**c["function"], "arguments": "{}"}} if self.bad_arguments(c) else c
                    for c in calls
                ]
                messages.append({"role": "assistant", "content": text or None, "tool_calls": kept})
                for call in calls:
                    output = await self.call_tool(call)
                    name = call["function"]["name"]
                    edits = edits + 1 if name in EDIT_TOOLS else (0 if name == "verify" else edits)
                    reading = name.rsplit("__", 1)[-1] in READ_TOOLS
                    limit = self.settings.max_read_chars if reading else self.settings.max_tool_chars
                    content = trim(output, limit)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                if edits >= self.settings.edits_before_verify and st.load(self.root):
                    result = await self.verify()
                    messages.append({"role": "user", "content": prompts.AUTO_VERIFY.format(edits=edits, result=result)})
                    edits = 0
            else:
                messages.append({"role": "assistant", "content": text})
                state = st.load(self.root)
                if state is not None and st.finished(state):
                    return Outcome(True, "ALL DONE", text)
                nudges += 1
                if nudges > self.settings.max_nudges:
                    return Outcome(False, "the model stopped before the task was finished")
                if state is None:
                    messages.append({"role": "user", "content": prompts.NEEDS_PLAN})
                else:
                    result = await self.verify()
                    messages.append({"role": "user", "content": prompts.STOPPED.format(result=result)})
                    edits = 0

            state = st.load(self.root)
            if state is not None:
                if state["repeats"] >= wf.REPEAT_STOP:
                    return Outcome(False, f"the same failure happened {state['repeats']} times in a row")
                if st.finished(state):
                    messages.append({"role": "user", "content": prompts.SUMMARY})
                    wrap_up = True

        return Outcome(False, f"turn limit ({self.settings.max_turns}) reached")

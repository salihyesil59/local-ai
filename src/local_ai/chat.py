"""Chat mode: a plain conversation with the model, like the chat of LM Studio. No plan, no agent loop and no tools
of its own; images go with a message, and the MCP tools the user switched on are offered (the model may call them)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from local_ai.core.images import with_images
from local_ai.core.llm import LLM, TokenBatcher
from local_ai.core.text import parse_inline_tool_calls, size, strip_think, strip_tool_calls, trim
from local_ai.core.tools import Tool, parse_arguments

SYSTEM = (
    "You are a helpful assistant. Answer in the language of the user's message. Write formulas in LaTeX ($..$ "
    "inline, $$..$$ on their own line) and code in fenced blocks."
)
MAX_TOOL_ROUNDS = 8  # replies with tool calls in a row; the reply after them is given no tools
MAX_TOOL_CHARS = 8000  # of one tool result kept in the conversation
CUT_OFF = "\n\n*(The reply was cut off at the token limit: raise Max tokens in the settings.)*"
ANSWER_NOW = (
    "Your last reply was cut off while you were still thinking. Do not think it through again: answer now, "
    "briefly, from what you know and what the tools returned."
)


class ChatAgent:
    """The conversation of one chat; `messages` goes on with the next message, also after a stop."""

    def __init__(
        self,
        llm: LLM,
        emit: Callable[..., None] | None = None,
        tools: dict[str, Tool] | None = None,
        context_chars: int = 300_000,
    ):
        self.llm = llm
        self.emit = emit or (lambda kind, **data: None)
        self.tools = tools or {}
        self.context_chars = context_chars
        self.messages: list[dict] = []
        # messages the user sent while a reply was being written, with their images: read before the next tool
        # round, or answered one after the other when the reply is done
        self.steering: list[tuple[str, list[Path]]] = []

    def steer(self, message: str, images: list[Path] | None = None) -> None:
        self.steering.append((message, list(images or [])))

    def fit(self) -> None:
        """Drop the oldest messages (not the instructions) when the conversation outgrows the context; a kept
        conversation starts with a message of the user."""
        while size(self.messages) > self.context_chars and len(self.messages) > 2:
            del self.messages[1]
            while len(self.messages) > 2 and self.messages[1]["role"] != "user":
                del self.messages[1]

    async def reply(self, text: str, images: list[Path] | None = None) -> str:
        """Answer one message of the user; returns the answer (also kept in the conversation)."""
        if not self.messages:
            self.messages.append({"role": "system", "content": SYSTEM})
        self.messages.append({"role": "user", "content": with_images(text, images)})
        specs = [t.spec() for t in self.tools.values()] or None
        nudge = None  # the request to answer at once, after a reply that only thought
        for turn in range(1, MAX_TOOL_ROUNDS + 3):
            if turn > 1:  # after a tool round the model reads what the user sent in the meantime
                while self.steering:
                    message, shown = self.steering.pop(0)
                    self.emit("dequeued")
                    self.messages.append({"role": "user", "content": with_images(message, shown)})
            self.fit()
            context = size(self.messages)
            offered = specs if turn <= MAX_TOOL_ROUNDS else None
            batch = TokenBatcher(lambda chunk, thinking: self.emit("token", text=chunk, thinking=thinking))
            try:
                reply = await self.llm.complete(self.messages, offered, batch.add, unstick=nudge is not None)
            finally:
                batch.flush()
                self.emit("stream_end")
            calls = (reply.tool_calls or parse_inline_tool_calls(reply.content)) if offered else []
            self.emit(
                "turn",
                turn=turn,
                context=context,
                context_limit=self.context_chars,
                prompt_tokens=reply.prompt_tokens,
                seconds=reply.seconds,
                tokens=reply.completion_tokens,
                thinking_chars=reply.reasoning_chars,
                finish=reply.finish_reason,
                tool_calls=[c["function"]["name"] for c in calls],
            )
            answer = strip_tool_calls(strip_think(reply.content))
            if calls and reply.finish_reason != "length":
                self.messages.append({"role": "assistant", "content": answer or None, "tool_calls": calls})
                for call in calls:
                    self.messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": trim(await self.call(call), MAX_TOOL_CHARS)}
                    )
                continue
            if reply.finish_reason == "length" and not answer and nudge is None:
                nudge = {"role": "user", "content": ANSWER_NOW}  # once, sampled more freely
                self.messages.append(nudge)
                continue
            if nudge in self.messages:  # the conversation keeps the answer, not the request for it
                self.messages.remove(nudge)
            if reply.finish_reason == "length":
                answer += CUT_OFF
            self.messages.append({"role": "assistant", "content": answer})
            return answer
        raise AssertionError("not reached: the last turn has no tools")

    async def call(self, call: dict) -> str:
        """Run one tool call; a mistake of the model comes back to it as text."""
        name = call["function"]["name"]
        raw = call["function"].get("arguments") or "{}"
        self.emit("tool_call", id=call["id"], name=name, arguments=raw)
        tool = self.tools.get(name)
        args, problem = parse_arguments(raw)
        if tool is None:
            output = f"Error: unknown tool {name!r}. Available tools: {', '.join(self.tools)}"
        elif problem:
            output = problem
        else:
            try:
                result = tool.fn(**args) if asyncio.iscoroutinefunction(tool.fn) else asyncio.to_thread(tool.fn, **args)
                output = await result
            except TypeError as e:
                output = f"Error: wrong arguments for {name}: {e}"
        self.emit("tool_result", id=call["id"], name=name, output=output, error=output.startswith("Error"))
        return output

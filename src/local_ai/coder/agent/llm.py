"""One streamed completion from an OpenAI-compatible server (LM Studio)."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""  # "length" when max_tokens cut the reply off
    reasoning_chars: int = 0
    completion_tokens: int | None = None
    seconds: float = 0.0


class LLM:
    def __init__(
        self,
        client,  # openai.AsyncOpenAI, or anything with the same interface
        model: str,
        max_tokens: int = 8192,
        temperature: float = 0.6,
        top_p: float = 0.95,
        top_k: int = 20,
        presence_penalty: float | None = None,
        max_thinking_chars: int = 8_000,
    ):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.presence_penalty = presence_penalty
        self.max_thinking_chars = max_thinking_chars  # about 2300 tokens of thinking without an answer or call

    @staticmethod
    async def close(stream) -> None:
        """Close the stream so the server stops generating (one that ended normally is closed already)."""
        closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta: Callable[[str, str], None] | None = None,
        max_tokens: int | None = None,
    ) -> Reply:
        """Stream one completion; on_delta(kind, text) gets "thinking" and "text" pieces as they arrive.
        max_tokens covers thinking too, so a model stuck in a loop is cut off."""
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=max_tokens or self.max_tokens,
            extra_body={"top_k": self.top_k},
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty

        started = time.monotonic()
        reply = Reply()
        content: list[str] = []
        calls: dict[int, dict] = {}
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                reply.completion_tokens = chunk.usage.completion_tokens
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                reply.reasoning_chars += len(reasoning)
                if on_delta:
                    on_delta("thinking", reasoning)
                if reply.reasoning_chars > self.max_thinking_chars and not content and not calls:
                    reply.finish_reason = "length"  # thinking for too long: stop generating now, do not wait
                    break
            if delta.content:
                content.append(delta.content)
                if on_delta:
                    on_delta("text", delta.content)
            for tc in delta.tool_calls or []:
                call = calls.setdefault(
                    tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                call["id"] = tc.id or call["id"]
                if tc.function is not None:
                    call["function"]["name"] += tc.function.name or ""
                    call["function"]["arguments"] += tc.function.arguments or ""
            if choice.finish_reason:
                reply.finish_reason = choice.finish_reason
        await self.close(stream)
        reply.content = "".join(content)
        reply.tool_calls = [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]
        reply.seconds = time.monotonic() - started
        return reply

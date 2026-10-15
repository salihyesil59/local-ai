"""The model connection: streamed completions from an OpenAI-compatible server (LM Studio)."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://localhost:1234/v1"
STREAM_FLUSH = 0.15  # seconds between "token" events while streaming
THINKING_TAIL = 1500  # characters of the thinking kept with a reply
LOOP_SPAN = 300  # thinking goes in circles when its last LOOP_SPAN characters have come LOOP_TIMES times
LOOP_TIMES = 3
LOOP_CHECK = 400  # characters of new thinking between two checks
UNSTICK_TEMPERATURE = 0.9  # for the reply after one that went in circles
UNSTICK_REPEAT_PENALTY = 1.1


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""  # "length" when max_tokens cut the reply off
    reasoning_chars: int = 0
    reasoning_tail: str = ""  # the end of the thinking, for a reply that was cut off before it acted
    reasoning: str = ""  # all of the thinking, kept with the reply for traces (it is not sent back to the model)
    looping: bool = False  # cut off because the thinking repeated itself
    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    seconds: float = 0.0


async def pick_model(client) -> str | None:
    """The first model the server has loaded that is not an embedding model; None when there is none."""
    ids = [m.id for m in (await client.models.list()).data if "embed" not in m.id.lower()]
    return ids[0] if ids else None


class TokenBatcher:
    """Collects streamed text and thinking and passes them on in batches: emit(text, thinking) every STREAM_FLUSH
    seconds, so a UI gets a few "token" events per second and not one per token."""

    def __init__(self, emit: Callable[[str, bool], None]):
        self.emit = emit
        self.pending = {"text": "", "thinking": ""}
        self.last = time.monotonic()

    def add(self, kind: str, text: str) -> None:
        self.pending[kind] += text
        if time.monotonic() - self.last > STREAM_FLUSH:
            self.flush()

    def flush(self) -> None:
        for kind, text in self.pending.items():
            if text:
                self.emit(text, kind == "thinking")
                self.pending[kind] = ""
        self.last = time.monotonic()


def goes_in_circles(thinking: str) -> bool:
    """True when the end of the thinking has been written several times already: the model is stuck in a loop."""
    tail = thinking[-LOOP_SPAN:]
    return len(thinking) >= LOOP_SPAN * LOOP_TIMES and thinking.count(tail) >= LOOP_TIMES


async def close(stream) -> None:
    """Close the stream so the server stops generating (one that ended normally is closed already)."""
    closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if closer is not None:
        result = closer()
        if inspect.isawaitable(result):
            await result


async def read_stream(
    stream, on_delta: Callable[[str, str], None] | None = None, max_thinking_chars: int | None = None
) -> Reply:
    """Rebuild the reply (content and tool calls) from the chunks of a streamed completion; on_delta(kind, text)
    gets "thinking" and "text" pieces as they arrive. A reply that only thinks is stopped after max_thinking_chars,
    and at once when the thinking goes in circles."""
    reply = Reply()
    checked = 0
    content: list[str] = []
    thinking: list[str] = []
    calls: dict[int, dict] = {}
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            reply.completion_tokens = chunk.usage.completion_tokens
            reply.prompt_tokens = getattr(chunk.usage, "prompt_tokens", None)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            reply.reasoning_chars += len(reasoning)
            thinking.append(reasoning)
            if on_delta:
                on_delta("thinking", reasoning)
            if max_thinking_chars and reply.reasoning_chars > max_thinking_chars and not content and not calls:
                reply.finish_reason = "length"  # thinking for too long: stop generating now, do not wait
                break
            if max_thinking_chars and reply.reasoning_chars - checked >= LOOP_CHECK and not content and not calls:
                checked = reply.reasoning_chars
                if goes_in_circles("".join(thinking)):
                    reply.finish_reason, reply.looping = "length", True
                    break
        if delta.content:
            content.append(delta.content)
            if on_delta:
                on_delta("text", delta.content)
        for tc in delta.tool_calls or []:
            call = calls.setdefault(tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            call["id"] = tc.id or call["id"]
            if tc.function is not None:
                call["function"]["name"] += tc.function.name or ""
                call["function"]["arguments"] += tc.function.arguments or ""
        if getattr(choice, "finish_reason", None):
            reply.finish_reason = choice.finish_reason
    await close(stream)
    reply.content = "".join(content)
    reply.reasoning = "".join(thinking)
    reply.reasoning_tail = reply.reasoning[-THINKING_TAIL:]
    reply.tool_calls = [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]
    return reply


def sent(message: dict) -> dict:
    """A message as the server gets it: the thinking a message keeps for traces stays here."""
    return {k: v for k, v in message.items() if k != "reasoning"} if "reasoning" in message else message


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

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta: Callable[[str, str], None] | None = None,
        max_tokens: int | None = None,
        unstick: bool = False,
    ) -> Reply:
        """Stream one completion; on_delta(kind, text) gets "thinking" and "text" pieces as they arrive.
        max_tokens covers thinking too, so a model stuck in a loop is cut off. unstick: the reply before went in
        circles, so this one is sampled more freely and repeating is made costly."""
        extra = {"top_k": self.top_k}
        if unstick:
            extra["repeat_penalty"] = UNSTICK_REPEAT_PENALTY
        kwargs = dict(
            model=self.model,
            messages=[sent(m) for m in messages],
            temperature=max(self.temperature, UNSTICK_TEMPERATURE) if unstick else self.temperature,
            top_p=self.top_p,
            max_tokens=max_tokens or self.max_tokens,
            extra_body=extra,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty

        started = time.monotonic()
        stream = await self.client.chat.completions.create(**kwargs)
        reply = await read_stream(stream, on_delta, self.max_thinking_chars)
        reply.seconds = time.monotonic() - started
        return reply

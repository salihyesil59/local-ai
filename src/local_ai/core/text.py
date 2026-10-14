"""Text helpers for model replies and tool output: they keep the context small."""

from __future__ import annotations

import json
import re
import uuid

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
INLINE_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
FUNCTION_CALL_RE = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.DOTALL)
PARAMETER_RE = re.compile(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", re.DOTALL)
IMAGE_CHARS = 4500  # what an image in a message counts for in the context size (about 1500 tokens)
TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>|<function=[\w.\-]+>.*?</function>", re.DOTALL)


def strip_think(text: str | None) -> str:
    """Drop Qwen-style <think> blocks; reasoning is not sent back to the model."""
    if not text:
        return ""
    text = THINK_RE.sub("", text)
    if "</think>" in text:  # opening tag was consumed by the chat template
        text = text.split("</think>", 1)[1]
    return text.strip()


def trim(text: str, max_chars: int) -> str:
    """Keep the head and tail of long tool output."""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    dropped = len(text) - head - tail
    return f"{text[:head]}\n\n[... {dropped} characters omitted; request a narrower section if needed ...]\n\n{text[-tail:]}"


def _parameter(value: str):
    """A parameter written as text: a list, an object, a number or a boolean when it reads as one."""
    if value[:1] in "[{" or re.fullmatch(r"-?\d+(\.\d+)?|true|false|null", value):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def strip_tool_calls(text: str) -> str:
    """The text without the tool calls a model wrote into it."""
    return TOOL_CALL_BLOCK_RE.sub("", text).strip()


def parse_inline_tool_calls(content: str) -> list[dict]:
    """Fallback for when the server leaves a tool call in the message content: Qwen's
    <tool_call>{...}</tool_call>, or <function=name><parameter=key>value</parameter></function>."""
    calls = []
    for name, body in FUNCTION_CALL_RE.findall(content or ""):
        args = {key: _parameter(value) for key, value in PARAMETER_RE.findall(body)}
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        )
    for raw in INLINE_TOOL_CALL_RE.findall(content or ""):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "name" not in data:
            continue
        args = data.get("arguments", {})
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": data["name"],
                    "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return calls


def extract_json(text: str):
    """Parse the first JSON object/array in a model reply (tolerates code fences and prose)."""
    text = strip_think(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON found in model reply.")


def size(messages: list[dict]) -> int:
    """Rough context size in characters."""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):  # text and images
            total += sum(len(p.get("text", "")) if p.get("type") == "text" else IMAGE_CHARS for p in content)
        else:
            total += len(content)
        for call in m.get("tool_calls") or []:
            total += len(call["function"]["name"]) + len(call["function"]["arguments"])
    return total

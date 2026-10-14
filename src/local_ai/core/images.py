"""Images sent with a message (a screenshot of an error, a plot, a page of a paper): checked when they arrive, kept
as files, and given to a vision model as data URLs in the OpenAI chat format."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from local_ai.core.text import strip_think

TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MIME = {ext: mime for mime, ext in TYPES.items()}
MAX_IMAGES = 6  # per message
MAX_BYTES = 10 * 1024 * 1024  # per image, decoded
DATA_URL = re.compile(r"data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=\s]+)")

DESCRIBE = (
    "The user attached {what} to the question below. Write down what {it} shows so that someone who cannot see "
    "{it} can work with it: first copy every piece of text exactly (code, error messages and tracebacks, numbers, "
    "labels; formulas in LaTeX), then describe plots, diagrams and tables (axes, values, trends). The question is "
    "answered by another agent from your description: do not answer it, explain or suggest anything, and do not "
    "guess what is not visible.\n\nThe question, only so you know what matters: {question}"
)
DESCRIBED = "\n\nThe user attached {what}. What {it} shows:\n{text}"


def decode(images) -> list[tuple[bytes, str]]:
    """The images of a request (data URLs) as (bytes, file extension); ValueError says what is wrong."""
    if not images:
        return []
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError(f"send at most {MAX_IMAGES} images, as a list of data URLs")
    found = []
    for url in images:
        m = DATA_URL.fullmatch(url) if isinstance(url, str) else None
        if not m or m.group(1) not in TYPES:
            raise ValueError("an image must be a PNG, JPEG, WebP or GIF data URL")
        try:
            data = base64.b64decode(m.group(2), validate=False)
        except binascii.Error as e:
            raise ValueError(f"an image is not valid base64: {e}") from None
        if not data or len(data) > MAX_BYTES:
            raise ValueError(f"an image is empty or larger than {MAX_BYTES // (1024 * 1024)} MB")
        found.append((data, TYPES[m.group(1)]))
    return found


def data_url(path: Path) -> str:
    return f"data:{MIME[path.suffix[1:]]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def with_images(text: str, paths: list[Path] | None) -> str | list[dict]:
    """The content of a user message: the text, and the images after it when there are any."""
    if not paths:
        return text
    return [{"type": "text", "text": text}, *({"type": "image_url", "image_url": {"url": data_url(p)}} for p in paths)]


def text_of(content) -> str:
    """The text of a message's content, also of one with images (they are left out)."""
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
    return content or ""


def count(content) -> int:
    return sum(part.get("type") == "image_url" for part in content) if isinstance(content, list) else 0


def words(n: int) -> tuple[str, str]:
    return ("an image", "it") if n == 1 else (f"{n} images", "they")


async def describe(llm, paths: list[Path], question: str) -> str:
    """What the images show, written by the (vision) model, for an agent that works with text only. Raises when the
    model cannot read images."""
    what, it = words(len(paths))
    prompt = DESCRIBE.format(what=what, it=it, question=question)
    reply = await llm.complete([{"role": "user", "content": with_images(prompt, paths)}])
    text = strip_think(reply.content)
    if not text:
        raise RuntimeError("the model wrote nothing about the images (is a vision model loaded?)")
    return text


def described(question: str, n: int, text: str) -> str:
    what, it = words(n)
    return question + DESCRIBED.format(what=what, it=it, text=text)

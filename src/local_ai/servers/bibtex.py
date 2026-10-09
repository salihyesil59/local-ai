"""Minimal BibTeX reading and writing (no external dependency)."""

from __future__ import annotations

import re
import unicodedata

LATEX_ACCENTS = {
    r"\"o": "ö",
    r"\"u": "ü",
    r"\"a": "ä",
    r"\c{c}": "ç",
    r"\c c": "ç",
    r"\u{g}": "ğ",
    r"\u g": "ğ",
    r"\i": "ı",
    r"\'e": "é",
    r"\`e": "è",
    r"\^o": "ô",
    r"\~n": "ñ",
    r"\ss": "ß",
    r"\c{s}": "ş",
    r"\c s": "ş",
}


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _read_braced(text: str, i: int) -> tuple[str, int]:
    """text[i] == '{'; return the content up to the matching brace."""
    depth, start = 0, i
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    raise ValueError("Unbalanced braces in BibTeX")


def _read_value(text: str, i: int, strings: dict[str, str]) -> tuple[str, int]:
    parts = []
    while True:
        i = _skip_ws(text, i)
        if text[i] == "{":
            value, i = _read_braced(text, i)
        elif text[i] == '"':
            j = i + 1
            depth = 0
            while j < len(text) and not (text[j] == '"' and depth == 0):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            value, i = text[i + 1 : j], j + 1
        else:
            m = re.match(r"[\w\-.:]+", text[i:])
            if not m:
                raise ValueError(f"Bad BibTeX value near: {text[i : i + 30]!r}")
            token = m.group(0)
            value, i = strings.get(token.lower(), token), i + len(token)
        parts.append(value)
        i = _skip_ws(text, i)
        if i < len(text) and text[i] == "#":
            i += 1
            continue
        return "".join(parts), i


def parse_bibtex(text: str) -> list[dict]:
    """Parse BibTeX into dicts: {"ENTRYTYPE", "ID", <lowercase field>: value}. Skips broken entries."""
    entries: list[dict] = []
    strings: dict[str, str] = {}
    i = 0
    while True:
        at = text.find("@", i)
        if at == -1:
            break
        m = re.match(r"@(\w+)\s*[{(]", text[at:])
        if not m:
            i = at + 1
            continue
        kind = m.group(1).lower()
        i = at + m.end()
        try:
            if kind in ("comment", "preamble"):
                _, i = _read_braced(text, at + m.end() - 1)
                continue
            if kind == "string":
                name_m = re.match(r"\s*(\w+)\s*=", text[i:])
                value, i = _read_value(text, i + name_m.end(), strings)
                strings[name_m.group(1).lower()] = value
                continue
            key_m = re.match(r"\s*([^,\s]+)\s*,", text[i:])
            entry = {"ENTRYTYPE": kind, "ID": key_m.group(1)}
            i += key_m.end()
            while True:
                i = _skip_ws(text, i)
                if i >= len(text) or text[i] in "})":
                    i += 1
                    break
                field_m = re.match(r"([\w\-:.]+)\s*=", text[i:])
                if not field_m:
                    raise ValueError("expected field")
                value, i = _read_value(text, i + field_m.end(), strings)
                entry[field_m.group(1).lower()] = value
                i = _skip_ws(text, i)
                if i < len(text) and text[i] == ",":
                    i += 1
            entries.append(entry)
        except (ValueError, AttributeError, IndexError):
            nxt = text.find("\n@", i)
            i = len(text) if nxt == -1 else nxt + 1
    return entries


def clean_latex(text: str) -> str:
    for k, v in LATEX_ACCENTS.items():
        text = text.replace(k, v)
    text = re.sub(r"\\(?:emph|textit|textbf|mathrm|text)\{([^{}]*)\}", r"\1", text)
    text = text.replace("{", "").replace("}", "").replace("\\&", "&").replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_authors(value: str) -> list[str]:
    return [clean_latex(a) for a in re.split(r"\s+and\s+", value.strip()) if a.strip()]


def pdf_paths_from_file_field(value: str) -> list[str]:
    """Extract PDF paths from Zotero/JabRef/BibLaTeX `file` fields.

    Forms: "/a/b.pdf", "Full Text:/a/b.pdf:application/pdf;Snapshot:...", ":a\\:b.pdf:PDF", "C\\:\\\\x\\\\y.pdf".
    """
    value = value.replace("\\:", "\x00").replace("\\\\", "\\")
    paths = []
    for part in value.split(";"):
        for piece in part.split(":"):
            piece = piece.replace("\x00", ":").strip()
            if piece.lower().endswith(".pdf"):
                paths.append(piece)
    return paths


def make_key(authors: list[str] | str, year: str | int | None, title: str = "") -> str:
    if isinstance(authors, str):
        authors = split_authors(authors)
    last = ""
    if authors:
        first = authors[0]
        last = first.split(",")[0] if "," in first else first.split()[-1] if first.split() else ""
    word = next((w for w in re.findall(r"[A-Za-zÀ-ž]+", title) if len(w) > 3), "")
    key = f"{last}{year or ''}{word}".replace("ı", "i")
    key = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", key).lower() or "ref"


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
        .replace("%", "\\%")
    )


def format_entry(entry_type: str, key: str, fields: dict[str, str]) -> str:
    lines = [f"@{entry_type}{{{key},"]
    for name, value in fields.items():
        if value:
            lines.append(f"  {name} = {{{_escape(str(value))}}},")
    lines.append("}")
    return "\n".join(lines)

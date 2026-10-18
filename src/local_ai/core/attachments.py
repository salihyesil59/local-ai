"""Files sent with a message (a PDF, a Word or PowerPoint file, a spreadsheet, notes, code): checked when they
arrive, kept as files, and given to the model as their text. What does not fit into the message is cut there; the
web UI puts the whole file into the library, where the model can search and read it page by page."""

from __future__ import annotations

import base64
import binascii
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DOCS = {".pdf", ".docx", ".pptx", ".xlsx"}
TEXT = {
    ".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".tex", ".bib", ".html", ".htm", ".xml",
    ".log", ".ipynb", ".py", ".js", ".ts", ".c", ".cpp", ".h", ".hpp", ".java", ".rs", ".go", ".m", ".jl", ".r",
    ".sh", ".ps1", ".bat", ".sql", ".ini", ".cfg",
}  # fmt: skip
SUFFIXES = DOCS | TEXT
MAX_FILES = 10  # per message
MAX_BYTES = 50 * 1024 * 1024  # per file, decoded
DATA_URL = re.compile(r"data:[^,]*;base64,([A-Za-z0-9+/=\s]*)")
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def safe_name(name: str) -> str:
    """The file name without folders or characters a file system refuses."""
    name = UNSAFE.sub("_", Path(str(name).replace("\\", "/")).name).strip(" .")
    return name[:120] or "file"


def decode(files) -> list[tuple[str, bytes]]:
    """The files of a request ([{name, data: data URL}]) as (safe name, bytes); ValueError says what is wrong."""
    if not files:
        return []
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError(f"send at most {MAX_FILES} files with a message")
    found = []
    for item in files:
        name = safe_name(item.get("name", "")) if isinstance(item, dict) else ""
        if Path(name).suffix.lower() not in SUFFIXES:
            raise ValueError(f"{name or 'a file'}: this kind of file cannot be attached (PDF, Word, PowerPoint, Excel, "
                             "text, Markdown or code files can)")  # fmt: skip
        m = DATA_URL.fullmatch(item.get("data", "")) if isinstance(item.get("data"), str) else None
        try:
            data = base64.b64decode(m.group(1), validate=False) if m else b""
        except binascii.Error as e:
            raise ValueError(f"{name} is not valid base64: {e}") from None
        if not data or len(data) > MAX_BYTES:
            raise ValueError(f"{name} is empty or larger than {MAX_BYTES // (1024 * 1024)} MB")
        found.append((name, data))
    return found


# -- text of a file ----------------------------------------------------------------------------------


def _xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    return ElementTree.fromstring(archive.read(name))


def _runs(element: ElementTree.Element, tag: str) -> str:
    return "".join(t.text or "" for t in element.iter(tag))


def _docx(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        body = _xml(z, "word/document.xml").find(f"{W}body")
    blocks = []
    for child in body if body is not None else []:
        if child.tag == f"{W}p":
            blocks.append(_runs(child, f"{W}t"))
        elif child.tag == f"{W}tbl":
            for row in child.iter(f"{W}tr"):
                blocks.append(" | ".join(_runs(cell, f"{W}t") for cell in row.iter(f"{W}tc")))
    text = "\n".join(blocks).strip()
    return text, f"{len(text.split())} words"


def _numbered(names: list[str], pattern: str) -> list[str]:
    found = [(int(m.group(1)), n) for n in names if (m := re.fullmatch(pattern, n))]
    return [n for _, n in sorted(found)]


def _pptx(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        slides = _numbered(z.namelist(), r"ppt/slides/slide(\d+)\.xml")
        parts = []
        for i, name in enumerate(slides, 1):
            lines = [_runs(p, f"{A}t") for p in _xml(z, name).iter(f"{A}p")]
            parts.append(f"--- slide {i} ---\n" + "\n".join(line for line in lines if line.strip()))
    return "\n\n".join(parts), f"{len(slides)} slides"


def _xlsx(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_runs(si, f"{S}t") for si in _xml(z, "xl/sharedStrings.xml").iter(f"{S}si")]
        targets = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in _xml(z, "xl/_rels/workbook.xml.rels").iter(f"{REL}Relationship"):
                targets[rel.get("Id")] = "xl/" + rel.get("Target", "").lstrip("/").removeprefix("xl/")
        workbook = _xml(z, "xl/workbook.xml")
        sheets = [(s.get("name", ""), targets.get(s.get(f"{R}id"), "")) for s in workbook.iter(f"{S}sheet")]
        parts = []
        for title, target in sheets:
            if target not in names:
                continue
            rows = []
            for row in _xml(z, target).iter(f"{S}row"):
                cells = []
                for c in row.iter(f"{S}c"):
                    value = c.find(f"{S}v")
                    if c.get("t") == "s" and value is not None:
                        cells.append(shared[int(value.text)] if value.text and int(value.text) < len(shared) else "")
                    elif c.get("t") == "inlineStr":
                        cells.append(_runs(c, f"{S}t"))
                    else:
                        cells.append((value.text or "") if value is not None else "")
                rows.append("\t".join(cells))
            parts.append(f"--- sheet {title} ---\n" + "\n".join(rows))
    return "\n\n".join(parts), f"{len(parts)} sheets"


def _pdf(path: Path) -> tuple[str, str]:
    import pymupdf

    with pymupdf.open(path) as doc:
        pages = [f"--- page {i} ---\n{page.get_text().strip()}" for i, page in enumerate(doc, 1)]
    return "\n\n".join(pages), f"{len(pages)} pages"


def extract(path: Path) -> tuple[str, str]:
    """The text of a file and a short note of its size (pages, slides, ...). Raises when the file is damaged."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix == ".pptx":
        return _pptx(path)
    if suffix == ".xlsx":
        return _xlsx(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, f"{len(text.splitlines())} lines"


# -- what the model gets -----------------------------------------------------------------------------


def shares(lengths: list[int], budget: int) -> list[int]:
    """How many characters of each file go into the message: short files whole, the rest split what is left."""
    out = [0] * len(lengths)
    left, waiting = budget, len(lengths)
    for i in sorted(range(len(lengths)), key=lambda i: lengths[i]):
        out[i] = min(lengths[i], left // waiting)
        left -= out[i]
        waiting -= 1
    return out


def block(items: list[dict], budget: int) -> str:
    """The text of the files for the message. An item: name, text, info, and library (its path in the library when
    the whole file was put there)."""
    parts = []
    for item, share in zip(items, shares([len(i["text"]) for i in items], budget), strict=True):
        name, text = item["name"], item["text"]
        if not text.strip():
            parts.append(f"[Attached file: {name} ({item['info']}) — no text could be read from it]")
            continue
        if len(text) <= share:
            parts.append(f"[Attached file: {name} ({item['info']})]\n{text}\n[end of {name}]")
            continue
        shown = f"only its first {share} of {len(text)} characters are shown here"
        if item.get("library"):
            folder = item["library"].rsplit("/", 1)[0]
            rest = (f'The whole file is in the library: read it with read_library (file="{item["library"]}", page '
                    f'by page) and search it with search_library (scope="{folder}"). Read the parts you need before '
                    "answering from them.")  # fmt: skip
        else:
            rest = "The rest of the file is not shown."
        parts.append(f"[Attached file: {name} ({item['info']}); {shown}]\n{text[:share]}\n[... cut here. {rest}]")
    return "\n\n" + "\n\n".join(parts) if parts else ""

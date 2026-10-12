"""Download and update offline data while you are online: Kiwix ZIM archives and arXiv metadata/PDFs.

local-ai data zim search <query> [--lang eng]
local-ai data zim download <name|url> [--dest DIR]
local-ai data zim update [--dest DIR] [--keep-old]
local-ai data arxiv harvest [--set physics] [--from YYYY-MM-DD]
local-ai data arxiv pdfs <id> [<id> ...] [--dest DIR]
local-ai data status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

from local_ai.servers.common import workspace_root, zim_paths

USER_AGENT = "local-ai/0.1 (+https://github.com/salihyesil59/local-ai)"
ZIM_NAME_RE = re.compile(r"^(?P<prefix>.+)_(?P<date>\d{4}-\d{2})\.zim$")
OAI = "{http://www.openarchives.org/OAI/2.0/}"
ARXIV = "{http://arxiv.org/OAI/arXiv/}"


def kiwix_base() -> str:
    return os.environ.get("LOCAL_AI_KIWIX_CATALOG", "https://library.kiwix.org").rstrip("/")


def oai_base() -> str:
    return os.environ.get("LOCAL_AI_ARXIV_OAI", "https://export.arxiv.org/oai2")


def arxiv_pdf_base() -> str:
    return os.environ.get("LOCAL_AI_ARXIV_PDF", "https://arxiv.org/pdf").rstrip("/")


def default_zim_dir() -> Path:
    """The first existing folder in LOCAL_AI_ZIM_DIR, else its first entry (default ~/wiki-zim)."""
    paths = [p for p in zim_paths() if p.suffix != ".zim"] or [Path("~/wiki-zim").expanduser()]
    return next((p for p in paths if p.is_dir()), paths[0])


def _request(url: str, headers: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})


def fetch(url: str, retries: int = 4, timeout: float = 60) -> bytes:
    """GET with retries; honours 503 Retry-After (arXiv's OAI endpoint uses it for rate limiting)."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(min(float(e.headers.get("Retry-After") or 2 ** (attempt + 1)), 120))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# -- ZIM -----------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def zim_catalog(query: str = "", lang: str = "", name: str = "", limit: int = 50) -> list[dict]:
    params = {"count": str(limit)}
    if query:
        params["q"] = query
    if lang:
        params["lang"] = lang
    if name:
        params["name"] = name
    root = ET.fromstring(fetch(f"{kiwix_base()}/catalog/v2/entries?{urllib.parse.urlencode(params)}"))
    books = []
    for entry in root:
        if _local(entry.tag) != "entry":
            continue
        book = {"title": "", "name": "", "flavour": "", "language": "", "updated": "", "url": "", "size": 0}
        for child in entry:
            tag = _local(child.tag)
            if tag in ("title", "name", "flavour", "language", "updated"):
                book[tag] = (child.text or "").strip()
            elif tag == "link" and child.get("type") == "application/x-zim":
                book["url"] = re.sub(r"\.meta4$", "", child.get("href", ""))
                book["size"] = int(child.get("length") or 0)
        if book["url"]:
            book["file"] = book["url"].rsplit("/", 1)[-1]
            books.append(book)
    return books


def download(url: str, dest: Path, quiet: bool = False) -> Path:
    """Resumable download to dest (via dest.part), verified against <url>.sha256 when published."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    try:
        resp = urllib.request.urlopen(_request(url, headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # already complete
            resp = None
        else:
            raise
    if resp is not None:
        with resp:
            if done and resp.status != 206:  # server ignored Range: start over
                done = 0
            total = done + int(resp.headers.get("Content-Length") or 0)
            last = time.monotonic()
            with part.open("ab" if done else "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if not quiet and time.monotonic() - last > 2:
                        pct = f" {100 * done / total:.1f}%" if total else ""
                        print(f"  {dest.name}: {_human(done)}{pct}", file=sys.stderr)
                        last = time.monotonic()
    expected = _sha256_for(url)
    if expected:
        h = hashlib.sha256()
        with part.open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        if h.hexdigest() != expected:
            part.unlink()
            raise RuntimeError(f"Checksum mismatch for {dest.name}; the partial file was removed, try again.")
    part.replace(dest)
    return dest


def _sha256_for(url: str) -> str | None:
    try:
        text = fetch(url + ".sha256", retries=1).decode().strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    m = re.match(r"([0-9a-fA-F]{64})", text)
    return m.group(1).lower() if m else None


def local_zims(folder: Path) -> list[dict]:
    out = []
    for path in sorted(folder.glob("*.zim")):
        m = ZIM_NAME_RE.match(path.name)
        out.append({"path": path, "prefix": m["prefix"] if m else path.stem, "date": m["date"] if m else ""})
    return out


def zim_update(folder: Path, keep_old: bool = False, quiet: bool = False) -> list[str]:
    messages = []
    for z in local_zims(folder):
        if not z["date"]:
            messages.append(f"skip {z['path'].name}: no date in file name")
            continue
        candidates = []
        for name in dict.fromkeys([z["prefix"], z["prefix"].rsplit("_", 1)[0]]):
            for book in zim_catalog(name=name, limit=200):
                m = ZIM_NAME_RE.match(book["file"])
                if m and m["prefix"] == z["prefix"] and m["date"] > z["date"]:
                    candidates.append((m["date"], book))
        if not candidates:
            messages.append(f"up to date: {z['path'].name}")
            continue
        _, newest = max(candidates, key=lambda c: c[0])
        new_path = download(newest["url"], folder / newest["file"], quiet)
        messages.append(f"updated: {z['path'].name} -> {new_path.name}")
        if not keep_old:
            z["path"].unlink()
    return messages


# -- arXiv -----------------------------------------------------------------------------


def _text(el, path: str) -> str:
    found = el.find(path)
    return " ".join((found.text or "").split()) if found is not None and found.text else ""


def parse_oai_records(xml: bytes) -> tuple[list[dict], str]:
    root = ET.fromstring(xml)
    error = root.find(f"{OAI}error")
    if error is not None and error.get("code") != "noRecordsMatch":
        raise RuntimeError(f"OAI-PMH error {error.get('code')}: {error.text}")
    records = []
    for rec in root.iter(f"{OAI}record"):
        header = rec.find(f"{OAI}header")
        if header is not None and header.get("status") == "deleted":
            continue
        meta = rec.find(f"{OAI}metadata/{ARXIV}arXiv")
        if meta is None:
            continue
        authors = []
        for a in meta.iter(f"{ARXIV}author"):
            last, first = _text(a, f"{ARXIV}keyname"), _text(a, f"{ARXIV}forenames")
            authors.append(f"{last}, {first}".strip(", "))
        records.append(
            {
                "id": _text(meta, f"{ARXIV}id"),
                "title": _text(meta, f"{ARXIV}title"),
                "authors": authors,
                "abstract": _text(meta, f"{ARXIV}abstract"),
                "categories": _text(meta, f"{ARXIV}categories"),
                "doi": _text(meta, f"{ARXIV}doi"),
                "published": _text(meta, f"{ARXIV}created"),
                "updated": _text(meta, f"{ARXIV}updated"),
            }
        )
    token_el = root.find(f".//{OAI}resumptionToken")
    token = (token_el.text or "").strip() if token_el is not None else ""
    return records, token


def arxiv_harvest(
    out: Path, set_spec: str = "physics", since: str = "", delay: float = 3.0, quiet: bool = False
) -> dict:
    """Incremental OAI-PMH harvest into a JSONL file (one paper per line, newest version wins)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    state_path = out.with_suffix(".state.json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    since = since or state.get(set_spec, "")
    papers: dict[str, dict] = {}
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    papers[d["id"]] = d
    params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": set_spec}
    if since:
        params["from"] = since
    url = f"{oai_base()}?{urllib.parse.urlencode(params)}"
    started = date.today().isoformat()
    new = pages = 0
    while url:
        records, token = parse_oai_records(fetch(url))
        pages += 1
        for r in records:
            new += r["id"] not in papers
            papers[r["id"]] = r
        if not quiet:
            print(f"  page {pages}: {len(records)} records ({len(papers)} total)", file=sys.stderr)
        url = (
            f"{oai_base()}?{urllib.parse.urlencode({'verb': 'ListRecords', 'resumptionToken': token})}" if token else ""
        )
        if url:
            time.sleep(delay)  # arXiv asks harvesters to pause between requests
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for d in papers.values():
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    tmp.replace(out)
    state[set_spec] = started
    state_path.write_text(json.dumps(state))
    return {"total": len(papers), "new": new, "pages": pages, "file": str(out)}


def arxiv_pdfs(ids: list[str], dest: Path, delay: float = 3.0, quiet: bool = False) -> list[Path]:
    paths = []
    for i, arxiv_id in enumerate(ids):
        target = dest / f"{arxiv_id.replace('/', '_')}.pdf"
        if target.exists():
            paths.append(target)
            continue
        paths.append(download(f"{arxiv_pdf_base()}/{arxiv_id}", target, quiet))
        if i < len(ids) - 1:
            time.sleep(delay)
    return paths


# -- status / CLI ------------------------------------------------------------------


def status(zim_dir: Path) -> str:
    lines = [f"ZIM folder: {zim_dir}"]
    today = datetime.now(timezone.utc).date()
    for z in local_zims(zim_dir) if zim_dir.is_dir() else []:
        age = ""
        if z["date"]:
            y, m = map(int, z["date"].split("-"))
            age = f", ~{(today.year - y) * 12 + today.month - m} months old"
        lines.append(f"  {z['path'].name} ({_human(z['path'].stat().st_size)}{age})")
    meta = workspace_root() / "arxiv" / "metadata.jsonl"
    if meta.exists():
        state_path = meta.with_suffix(".state.json")
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        count = sum(1 for _ in meta.open(encoding="utf-8"))
        lines.append(f"arXiv metadata: {count} papers, last harvest {state or 'unknown'} ({meta})")
    else:
        lines.append("arXiv metadata: none (local-ai data arxiv harvest)")
    lib = workspace_root() / "library.sqlite"
    lines.append(f"Library index: {_human(lib.stat().st_size) if lib.exists() else 'not built'} ({lib})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="local-ai data", description="Download/update offline data (needs internet).")
    sub = p.add_subparsers(dest="area", required=True)

    zim = sub.add_parser("zim").add_subparsers(dest="cmd", required=True)
    s = zim.add_parser("search", help="search the Kiwix catalog")
    s.add_argument("query")
    s.add_argument("--lang", default="", help="ISO 639-3, e.g. eng, tur")
    s.add_argument("--limit", type=int, default=20)
    d = zim.add_parser("download", help="download a ZIM by catalog file name or URL")
    d.add_argument("name")
    d.add_argument("--dest", type=Path)
    u = zim.add_parser("update", help="replace local ZIMs with newer releases")
    u.add_argument("--dest", type=Path)
    u.add_argument("--keep-old", action="store_true")

    ax = sub.add_parser("arxiv").add_subparsers(dest="cmd", required=True)
    h = ax.add_parser("harvest", help="harvest arXiv metadata (OAI-PMH) into JSONL")
    h.add_argument("--set", default="physics", help="OAI set, e.g. physics, physics:hep-th, math, cs")
    h.add_argument("--from", dest="since", default="", help="YYYY-MM-DD (default: since last harvest)")
    h.add_argument("--out", type=Path)
    h.add_argument("--delay", type=float, default=3.0)
    pd = ax.add_parser("pdfs", help="download PDFs for arXiv ids")
    pd.add_argument("ids", nargs="+")
    pd.add_argument("--dest", type=Path, default=Path("~/papers/arxiv"))
    pd.add_argument("--delay", type=float, default=3.0)

    st = sub.add_parser("status")
    st.add_argument("--dest", type=Path)

    args = p.parse_args(argv)
    if args.area == "status":
        print(status((args.dest or default_zim_dir()).expanduser()))
    elif args.area == "zim" and args.cmd == "search":
        for b in zim_catalog(args.query, args.lang, limit=args.limit):
            print(f"{b['file']:<55} {_human(b['size']):>10}  {b['updated'][:10]}  {b['title']}")
    elif args.area == "zim" and args.cmd == "download":
        dest_dir = (args.dest or default_zim_dir()).expanduser()
        url = args.name
        if not url.startswith(("http://", "https://")):
            m = ZIM_NAME_RE.match(args.name)
            matches = [
                b
                for b in zim_catalog(name=m["prefix"].rsplit("_", 1)[0] if m else args.name, limit=200)
                if b["file"] == args.name or b["name"] == args.name
            ]
            if not matches:
                print(f"Not found in catalog: {args.name} (try: local-ai data zim search ...)", file=sys.stderr)
                return 1
            url = max(matches, key=lambda b: b["file"])["url"]
        print(download(url, dest_dir / url.rsplit("/", 1)[-1]))
    elif args.area == "zim" and args.cmd == "update":
        for line in zim_update((args.dest or default_zim_dir()).expanduser(), args.keep_old):
            print(line)
    elif args.area == "arxiv" and args.cmd == "harvest":
        out = (args.out or workspace_root() / "arxiv" / "metadata.jsonl").expanduser()
        print(arxiv_harvest(out, args.set, args.since, args.delay))
        print(f"Index the abstracts with: local-ai-index --arxiv {out}")
    elif args.area == "arxiv" and args.cmd == "pdfs":
        dest = args.dest.expanduser()
        for path in arxiv_pdfs(args.ids, dest, args.delay):
            print(path)
        print(f"Index them with: local-ai-index {dest}")
    return 0

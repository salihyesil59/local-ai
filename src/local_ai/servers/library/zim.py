"""Kiwix ZIM archives (offline Wikipedia, DevDocs): loading, HTML to text, article lookup and sections.

Archives come from LOCAL_AI_ZIM_DIR. wikipedia_* files serve `wikipedia` (language from the ZIM metadata),
devdocs_* files serve `docs`; the library's `search` reads passages from all of them.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from html.parser import HTMLParser

from local_ai.servers.common import MAX_OUTPUT, http_get, log, truncate, zim_files


class _HtmlToText(HTMLParser):
    """Kiwix article HTML -> plain text. Formulas are kept as TeX ($...$), code blocks keep their whitespace;
    references, navigation boxes and sidebars are skipped. Every formula exists twice (MathML <annotation> TeX
    and a fallback image alt); only one is written."""

    SKIP_TAGS = {"script", "style", "nav", "footer"}
    # Footnote markers are <sup class="reference">; other superscripts are units and powers (J⋅Hz^−1, 10^−34)
    # and must stay, or the text contradicts itself.
    SKIP_SUP_CLASSES = {"reference", "noprint"}
    # Matched against whole class tokens. "reference" must not be in here: the Python docs use
    # <a class="reference internal"> for every link (Wikipedia's footnote markers are <sup>, skipped by tag).
    SKIP_CLASSES = {"navbox", "sidebar", "infobox", "references", "reflist", "metadata", "ambox", "mw-editsection"}
    VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}
    BLOCK = {"p", "div", "li", "br", "tr", "dd", "dt", "blockquote"}

    def __init__(self):
        super().__init__()
        self.out, self.skip_depth, self.in_tex, self.in_math, self.last_tex = [], 0, False, 0, None
        self.pre_depth, self.pres = 0, []  # code blocks: raw text, put into ``` fences at the end

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip_depth:  # inside a skipped element only the nesting depth is tracked
            if tag not in self.VOID:
                self.skip_depth += 1
            return
        classes = (a.get("class") or "").split()
        footnote = tag == "sup" and self.SKIP_SUP_CLASSES.intersection(classes)
        if tag in self.SKIP_TAGS or footnote or self.SKIP_CLASSES.intersection(classes):
            if tag not in self.VOID:
                self.skip_depth = 1
        elif tag == "pre":
            if not self.pre_depth:
                self.pres.append([])
                self.out.append(f"\n\x00{len(self.pres) - 1}\x00\n")
            self.pre_depth += 1
        elif self.pre_depth:
            return
        elif tag == "sup":
            self.out.append("^")
        elif tag == "math":
            self.in_math += 1
        elif tag == "annotation" and a.get("encoding") == "application/x-tex":
            self.in_tex, self.tex = True, []
        elif tag == "img" and (a.get("alt") or "").startswith("{\\displaystyle"):
            if a["alt"] != self.last_tex:
                self.out.append(f" ${a['alt']}$ ")
            self.last_tex = None
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "dt" and a.get("id"):  # API entries in the docs (e.g. pathlib.Path.glob) become sections
            self.out.append(f"\n\n##### {a['id']}\n")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if self.skip_depth:
            self.skip_depth -= 1
        elif tag == "pre" and self.pre_depth:
            self.pre_depth -= 1
        elif tag == "annotation" and self.in_tex:
            self.in_tex = False
            self.last_tex = "".join(self.tex).strip()
            self.out.append(f" ${self.last_tex}$ ")
        elif tag == "math" and self.in_math:
            self.in_math -= 1

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.pre_depth:
            self.pres[-1].append(data)
        elif self.in_tex:
            self.tex.append(data)
        elif not self.in_math:
            self.out.append(data)

    def text(self):
        text = re.sub(r"[ \t]+", " ", "".join(self.out))
        text = re.sub(r"\n\s*\n(\s*\n)*", "\n\n", text).strip()
        return re.sub(
            r"\x00(\d+)\x00", lambda m: "```\n" + "".join(self.pres[int(m.group(1))]).strip("\n") + "\n```", text
        )


def html_to_text(raw: str) -> str:
    parser = _HtmlToText()
    parser.feed(raw)
    return parser.text()


def html_sections(raw: str) -> list[tuple[str, str]]:
    """(heading, text) per section of an article; text before the first heading has heading ""."""
    out, heading, lines = [], "", []
    for line in html_to_text(raw).splitlines():
        m = re.match(r"^#{1,5} (.+?)\s*$", line)
        if m:
            if "\n".join(lines).strip():
                out.append((heading, "\n".join(lines).strip()))
            heading, lines = m.group(1), []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        out.append((heading, "\n".join(lines).strip()))
    return out


ZIM_LANG = {"en": "eng", "tr": "tur"}
_archives = None


def archives():
    """All ZIM archives in LOCAL_AI_ZIM_DIR, opened once: [(name, languages, archive)]."""
    global _archives
    if _archives is None:
        from libzim.reader import Archive

        _archives = []
        for path in zim_files():
            archive = Archive(path)
            language = bytes(archive.get_metadata("Language")).decode() if "Language" in archive.metadata_keys else ""
            _archives.append((path.stem, language.split(","), archive))
            log(f"ZIM loaded: {path.name} ({language}, {archive.article_count} articles)")
    return _archives


def local_zims(lang):
    """Wikipedia archives (wikipedia_*) for a language. Other ZIMs such as DevDocs stay out of Wikipedia search."""
    return [(name, a) for name, langs, a in archives() if name.startswith("wikipedia_") and ZIM_LANG[lang] in langs]


def doc_archives(library=""):
    """DevDocs archives (devdocs_en_<library>_<date>): [(library, archive)], optionally filtered by library."""
    result = []
    for name, _, archive in archives():
        m = re.fullmatch(r"devdocs_[a-z]+_(.+?)_\d{4}-\d\d", name)
        if m and (not library or library.casefold() == m.group(1)):
            result.append((m.group(1), archive))
    return result


def _words(text):
    return set(re.findall(r"\w+", text.casefold()))


SMALL_ARCHIVE = 20000  # entries; small archives (DevDocs) also get path matching
_path_names = {}


def path_matches(name, archive, q_fold):
    """[(score, path)] for entries whose last path segment equals or contains the query.

    DevDocs titles a page after its last entry: C's printf family lives at io/fprintf titled 'sprintf_s', so
    neither the title index nor the full-text index finds it for 'printf'. Paths carry the function names."""
    if archive.all_entry_count > SMALL_ARCHIVE or len(q_fold) < 3:
        return []
    if name not in _path_names:
        entries = (archive._get_entry_by_id(i) for i in range(archive.all_entry_count))
        _path_names[name] = [(e.path.rsplit("/", 1)[-1].casefold(), e.path) for e in entries if not e.is_redirect]
    hits = []
    for base, path in _path_names[name]:
        if base == q_fold:
            hits.append((8.0, path))
        elif q_fold in base:
            hits.append((2.0 + len(q_fold) / len(base), path))
    return sorted(hits, reverse=True)[:5]


def zim_lookup(archives, query):
    """(title, text, source, other matches) or None.

    Title suggestions, full-text hits and (in small archives) path matches are collected from all given archives
    and scored: exact title match, share of title words found in the query, and search rank. (Stopping at the
    first archive with a hit picked unrelated articles, e.g. a mathematics article for a physics question.)"""
    from libzim.search import Query, Searcher
    from libzim.suggestion import SuggestionSearcher

    q_words, q_fold = _words(query), query.casefold().strip()
    candidates = {}
    for name, archive in archives:
        sources = []  # (base score per rank, paths)
        if archive.has_title_index:
            paths = SuggestionSearcher(archive).suggest(query).getResults(0, 5)
            sources += [(1.5 / (1 + rank), p) for rank, p in enumerate(paths)]
        if archive.has_fulltext_index:
            paths = Searcher(archive).search(Query().set_query(query)).getResults(0, 5)
            sources += [(1.0 / (1 + rank), p) for rank, p in enumerate(paths)]
        sources += path_matches(name, archive, q_fold)
        for base_score, path in sources:
            entry = archive.get_entry_by_path(path)
            while entry.is_redirect:
                entry = entry.get_redirect_entry()
            t_words = _words(entry.title)
            score = base_score
            score += 10 if entry.title.casefold() == q_fold else 0
            score += 3 * len(q_words & t_words) / len(t_words) if t_words else 0
            key = (name, entry.path)
            if key not in candidates or candidates[key][0] < score:
                candidates[key] = (score, entry, name)
    if not candidates:
        return None
    ranked = sorted(candidates.values(), key=lambda c: -c[0])
    _, entry, name = ranked[0]
    parser = _HtmlToText()
    parser.feed(bytes(entry.get_item().content).decode("utf-8", errors="replace"))
    others = list(dict.fromkeys(f"{e.title} [{n}]" for _, e, n in ranked[1:] if e.title != entry.title))[:6]
    return entry.title, parser.text(), name, others


# -- sections inside a page --------------------------------------------------------------------------------------

HEADING = re.compile(r"^(#{2,5}) (.+?)[ \t]*$", re.M)


def _section_match(heading, wanted):
    """3 exact, 2 suffix ('glob' / 'Path.glob' for 'pathlib.Path.glob'), 1 substring, 0 no match."""
    h, w = heading.casefold(), wanted.casefold().strip()
    if h == w:
        return 3
    if h.endswith("." + w) or h.endswith(" " + w):
        return 2
    return 1 if w and w in h else 0


def find_section(text, wanted, min_score=1):
    """Text from the best matching heading up to the next heading of the same or a higher level, or None."""
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in HEADING.finditer(text)]
    scored = [(_section_match(title, wanted), pos, level) for pos, level, title in heads]
    best = max(scored, key=lambda s: s[0], default=(0, 0, 0))
    if best[0] < min_score:
        return None
    _, start, level = best
    # An API entry (#####) contains its own "Notes"/"Examples" headings (####), so it ends at the next entry
    # or at a main section (### and up); other sections end at the next heading of the same or a higher level.
    ends = (lambda lvl: lvl == 5 or lvl <= 3) if level == 5 else (lambda lvl: lvl <= level)
    end = next((pos for pos, lvl, _ in heads if pos > start and ends(lvl)), len(text))
    return text[start:end].strip()


def outline(text, limit=80):
    titles = [m.group(2) for m in HEADING.finditer(text)]
    more = f" … (+{len(titles) - limit})" if len(titles) > limit else ""
    return "; ".join(titles[:limit]) + more


def render_page(header, text, query, section, others):
    """Full page, one section, or (for a query that names a section, e.g. 'pathlib.Path.glob') that section.
    Truncated pages end with their section list so the model can ask for a section."""
    more = f"\n\nOther matches: {', '.join(others)}" if others else ""
    if section:
        part = find_section(text, section)
        if part is None:
            return truncate(f"{header}\n\nSection '{section}' not found. Sections: {outline(text)}") + more
        return truncate(f"{header} — section '{section}'\n\n{part}") + more
    # Jump to the section a query names only when the whole page would not fit anyway
    part = find_section(text, query, min_score=2) if query and len(text) > MAX_OUTPUT else None
    if part:
        return truncate(f"{header} — section matching '{query}'\n\n{part}") + more
    if len(text) > MAX_OUTPUT and HEADING.search(text):
        return truncate(f"{header}\n\n{text}") + f"\n\nSections (pass section=...): {outline(text)}" + more
    return truncate(f"{header}\n\n{text}") + more


# -- Wikipedia (offline archive first, then online) ----------------------------------------------------------------


def wikipedia_online(lang, query, section=""):
    api = f"https://{lang}.wikipedia.org/w/api.php"
    found = json.loads(
        http_get(
            api
            + "?"
            + urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"}
            )
        )
    )
    titles = [r["title"] for r in found["query"]["search"]]
    if not titles:
        return "No article found."
    pages = json.loads(
        http_get(
            api
            + "?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "prop": "extracts",
                    "explaintext": 1,
                    "titles": titles[0],
                    "format": "json",
                    "redirects": 1,
                }
            )
        )
    )["query"]["pages"]
    text = next(iter(pages.values())).get("extract", "")
    # "== Heading ==" (plain-text extracts) -> "## Heading", the same section markers as the offline pages
    text = re.sub(r"^(={2,5}) *(.+?) *\1[ \t]*$", lambda m: "#" * len(m.group(1)) + " " + m.group(2), text, flags=re.M)
    return render_page(f"# {titles[0]} ({lang}.wikipedia.org)", text, "", section, titles[1:])


def wikipedia(query: str, lang: str = "en", online: bool = False, section: str = "") -> str:
    """Return the Wikipedia article that best matches the query, as text with formulas in TeX ($...$).
    Uses the offline Wikipedia archive first (works without internet); falls back to the online Wikipedia.
    lang: 'en' or 'tr' (English articles are usually more detailed for physics). Set online=True to skip the
    offline archive, e.g. for very recent topics. section: return only that section of the article (e.g.
    'Incompressible flow'); long articles end with their section list. If the article is not the right one,
    query one of the listed other matches."""
    lang = lang if lang in ZIM_LANG else "en"
    if not online:
        found = zim_lookup(local_zims(lang), query)
        if found:
            title, text, source, others = found
            return render_page(f"# {title} (offline archive: {source})", text, "", section, others)
    try:
        return wikipedia_online(lang, query, section)
    except OSError as e:  # URLError / timeout: no internet
        archives = ", ".join(name for name, _ in local_zims(lang)) or "none"
        return (
            f"Not found in the offline archive (archives: {archives}) and online Wikipedia is unreachable "
            f"({type(e).__name__}). Try other terms or the other language."
        )


# -- programming documentation (DevDocs) -----------------------------------------------------------------------------


def docs(query: str, library: str = "", section: str = "") -> str:
    """Look up offline programming documentation (DevDocs: Python standard library, numpy, pandas, matplotlib,
    git, bash, LaTeX, ...). Returns the best matching page with code examples. library: optional filter such as
    'python', 'numpy', 'pandas'; leave empty to search all. Query with names like 'numpy.linalg.solve' or
    'pathlib.Path.glob': a query that names an entry inside a page returns just that entry. section: return only
    that section of the page (e.g. 'Path.glob'); long pages end with their section list. For libraries without
    DevDocs (e.g. scipy, sympy), read their docstrings with Python: help(scipy.integrate.solve_ivp)."""
    found_archives = doc_archives(library)
    if not found_archives:
        available = sorted({lib for lib, _ in doc_archives()})
        if not available:
            return "No offline documentation installed (no devdocs_*.zim files in LOCAL_AI_ZIM_DIR)."
        return f"No offline documentation for '{library}'. Available libraries: {', '.join(available)}."
    found = zim_lookup(found_archives, query)
    if not found:
        return "No match. Try the full function/module name (e.g. 'numpy.linalg.solve')."
    title, text, source, others = found
    auto = query if title.casefold() != query.casefold().strip() else ""
    return render_page(f"# {title} (offline docs: {source})", text, auto, section, others)

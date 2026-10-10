# MCP servers

All servers are stdio MCP servers installed as commands: `.venv\Scripts\local-ai-<name>.exe` on Windows,
`.venv/bin/local-ai-<name>` on Linux. They read the `LOCAL_AI_*` variables listed in the
[README](../README.md#configuration). Each job is done by exactly one tool:

| Job | Tool |
|---|---|
| find passages in your library (and offline arXiv abstracts, ZIM archives) | `library` → `search_library` |
| read pages or a passage in context | `library` → `read_library` |
| a Wikipedia article or section | `library` → `wikipedia` |
| programming documentation | `library` → `docs` (also served alone by `docs`, for Goose) |
| arXiv online | `library` → `search_arxiv`, `read_arxiv` |
| calculations, units, algebra | `compute` → `python`, `check_units`, `solve` |
| notes, sources, reports, memory, skills | `notebook` |

## library

[`src/local_ai/servers/library`](../src/local_ai/servers/library): everything the model can look up.

| Tool | Purpose |
|---|---|
| `search_library` | passages from your library (`LOCAL_AI_LIBRARY`: PDF / .md / .txt, subfolders included, plus Zotero / BibTeX items) and offline arXiv abstracts; `sources=zim` also searches inside the offline Wikipedia and documentation archives. Any language, plus an optional `english_query`. Hits show a chunk id, page and source, and authors / year / DOI / citekey when known |
| `read_library` | a hit with the passages around it (chunk id), or pages of a library file (`source` as a hit shows it) |
| `wikipedia` | English / Turkish Wikipedia article from the `wikipedia_*` ZIM archives (TeX formulas kept); online as fallback, `online=True` skips the archive for very recent topics |
| `docs` | offline programming documentation from the `devdocs_*` ZIM archives (Python, numpy, pandas, matplotlib, ...); code blocks keep their indentation |
| `search_arxiv` / `read_arxiv` | arXiv API search (`newest_first` for recent work) and PDF page reading; needs internet, PDFs are cached in `<workspace>\cache\arxiv` |

**Indexing.** `LOCAL_AI_LIBRARY` is indexed automatically: a search first picks up new and changed files
(checked at most every 30 seconds; a large first index is spread over several searches, up to ~20 s each).
`local-ai-index` indexes it in one go, about 20 s for 560 files. Scanned PDFs have no text and need OCR first.
Other collections are added from the shell:

```powershell
local-ai-index                              # LOCAL_AI_LIBRARY now
local-ai-index $HOME\papers                 # another folder
local-ai-index --zotero $HOME\Zotero        # Zotero library: PDFs + authors, year, DOI
local-ai-index --bib refs.bib               # BibTeX (linked PDFs in full, others by abstract)
local-ai-index --arxiv $HOME\local-ai-workspace\arxiv\metadata.jsonl --categories hep-th,quant-ph --since 2018-01-01
local-ai-index --status
```

On Linux the paths are `~/papers`, `~/Zotero`, ... The indexer runs outside LM Studio, so set the `LOCAL_AI_*`
variables in the shell too if you changed them in `mcp.json` (the Terminal shortcut does this).

**Ranking.** Keyword search (SQLite FTS5 / BM25) always runs. With an embedding model, semantic ranking is added
and both are merged with reciprocal-rank fusion; optional cross-encoder re-ranking with `LOCAL_AI_RERANK_MODEL`
(e.g. `BAAI/bge-reranker-v2-m3`, extra `[rerank]`). The example configs set `LOCAL_AI_EMBED_BACKEND=none`:
next to a 35B model an embedding model costs speed, and the model bridges languages itself through
`english_query`.

**ZIM archives** are too big to index fully. A search with `sources=zim` uses each archive's own full-text index
to fetch candidate articles, splits them into sections and caches those in the index.

**Sections.** `wikipedia` and `docs` cut pages at ~12,000 characters and take a `section` argument:

- Section markers are the page headings, plus every API entry in the docs (e.g. `pathlib.Path.glob`).
- `section` matches exactly, as a suffix (`glob`, `Path.glob`) or as a substring, and returns only that part.
- A truncated page ends with its section list, so the model can ask for the right part.
- `docs` jumps on its own when the query names an entry on a long page: `docs("pathlib.Path.glob")` returns that
  ~2,000-character entry instead of the first 12,000 characters of the 127,000-character `pathlib` page.
- An API entry keeps its own Notes / Examples and ends at the next entry.

In small archives (DevDocs) entries are also matched by path, because DevDocs titles a page after its last entry:
C's `printf` family is a page titled `sprintf_s`. DevDocs has no scipy or sympy, so read their docstrings with
`help()` in `python`.

**Where the data lives.** Everything is in SQLite files in `LOCAL_AI_WORKSPACE`; there is no separate vector
database:

- `library.sqlite` has a `docs` table (files, ZIM articles, references) and a `chunks` table: each chunk's text,
  page and, with embeddings, its vector (float16, 2 KB per chunk for Qwen3-Embedding-0.6B). `chunks_fts` is the
  BM25 index.
- `memory.sqlite` stores the notebook's memories the same way.

**Changing the embedding model.** Vectors of different models are not comparable. Each database records the
model that produced its vectors (`meta` table). After a model change, semantic ranking turns off and keyword
search keeps working until `local-ai-index --reembed` embeds the library and memory again. Qwen3-Embedding
expects a task instruction before queries; the servers add one automatically (`LOCAL_AI_EMBED_QUERY_TASK`).

## docs

[`src/local_ai/servers/docs/server.py`](../src/local_ai/servers/docs/server.py) serves only the `docs` tool, for
coding agents (Goose) that need documentation without the other tools inflating the prompt.

## compute

[`src/local_ai/servers/compute/server.py`](../src/local_ai/servers/compute/server.py): reasoning you can check.

| Tool | Purpose |
|---|---|
| `python` | persistent Jupyter kernel: variables survive between calls; `np`, `scipy`, `sp` (sympy), `ureg` / `Q_` (pint) and `plt` preloaded; figures saved to `<workspace>\figures`. In the tools eval Python lifted GPQA physics from 55% to 79% |
| `check_units` | dimensional analysis: `check_units("G*M/r**2", {"G": "m^3/(kg*s^2)", "M": "kg", "r": "m"}, "m/s^2")` → OK / MISMATCH |
| `solve` | symbolic solving with sympy, returns plain text + LaTeX |
| `reset` | restart the kernel |
| `export_notebook` | save the cells run since the last reset as a Jupyter notebook (`.ipynb`) |

Code runs as your user in `<workspace>\compute`; it is not a sandbox.

## notebook

[`src/local_ai/servers/notebook/server.py`](../src/local_ai/servers/notebook/server.py): workspace, memory and
skills, used by the research agent.

| Tool | Purpose |
|---|---|
| `create_project`, `write_note`, `append_note`, `read_note`, `list_notes`, `list_projects` | per-question notes |
| `add_source`, `list_sources` | register sources (with exact quotes) → citation number `[n]` |
| `save_report`, `read_report` | final report with an auto-generated References section |
| `export_report` | html (self-contained), md, bib, and tex / docx / pdf via pandoc |
| `remember`, `recall`, `forget` | long-term memory across projects: facts (with sources), lessons, user preferences |
| `list_skills`, `load_skill` | research procedures |

Built-in skills ([`src/local_ai/skills`](../src/local_ai/skills)): `derivation`, `dimensional-analysis`,
`fermi-estimate`, `literature-review`, `paper-summary`, `compare-theories`, `numerical-simulation`. Add your own as
Markdown with front matter in `<workspace>\skills\` or a folder in `LOCAL_AI_SKILLS`; a skill with the same name
overrides the built-in one:

```markdown
---
name: my-skill
description: what it is for
when_to_use: when the agent should pick it
---
Step-by-step instructions...
```

Optional front matter `max_steps`, `max_tool_chars` and `max_tokens` raises the agent's limits for the
sub-question that uses the skill (`paper-summary` does, to read a whole paper).

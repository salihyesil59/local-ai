# local-ai

A unified local AI platform that runs entirely on your own computer: a local model in LM Studio (built for
[`salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF`](https://huggingface.co/salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF))
with two specialist agents — a **research agent** that searches your PDFs, offline Wikipedia and arXiv, computes
and unit-checks, and writes cited reports; and a **coding agent** that reads papers, writes code, runs tests,
and self-verifies — all sharing the same MCP tool servers.

## Modes

| | LM Studio chat | Research agent | Code agent | `local-ai ui` (browser) |
|---|---|---|---|---|
| Best for | Quick questions | Deep multi-step research | Writing & testing code | All of the above with a live view |
| Trigger | Direct chat | `local-ai` CLI | `local-coder` CLI | `local-ai ui` → Research / Code tab |
| Output | Chat answer | `report.md`, exports, notebook | Source files, test results | Same, plus export buttons |

## Servers

| Command | Server | Tools | For |
|---|---|---|---|
| `local-ai-library` | `library` | `search_library`, `read_library`, `wikipedia`, `docs`, `search_arxiv`, `read_arxiv` | Your PDFs, offline Wikipedia & docs, arXiv |
| `local-ai-compute` | `compute` | `python` (persistent kernel), `check_units`, `solve`, `reset`, `export_notebook` | Calculations you can verify |
| `local-ai-notebook` | `notebook` | notes, sources, reports, exports, memory, skills | Research agent internals |
| `local-ai-docs` | `docs` | `docs` | Coding agents (Goose, LM Studio) |
| `local-coder` | `coder` | `plan`, `verify`, `status`; with `--files` also `read_file`, `write_file`, `edit_file`, `list_files` | Coding in Goose / LM Studio (`local-coder run` is the coding agent itself) |

Details: [docs/servers.md](docs/servers.md).

## Setup (Windows)

Requirements: Python 3.10+ (3.13 recommended), [LM Studio](https://lmstudio.ai). For the optional `web` server,
[uv](https://docs.astral.sh/uv/) is also needed. All commands are for PowerShell.

```powershell
cd $HOME\Github\ai
git clone https://github.com/salihyesil59/local-ai
cd local-ai
py -3.13 -m venv .venv
.venv\Scripts\pip install -e ".[all]"
```

Use `".[dev]"` to also install test and lint tools. Commands are installed in `.venv\Scripts\`
(`local-ai.exe`, `local-coder.exe`, `local-ai-library.exe`, …). Either call them with the full path or activate
the environment first with `.venv\Scripts\Activate.ps1`. If PowerShell blocks scripts, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

1. **LM Studio:** load the chat model with the settings in [config/lmstudio/preset.md](config/lmstudio/preset.md).
   Keyword search needs nothing else. Semantic ranking also needs the embedding model **Qwen3-Embedding-0.6B**;
   the example configs turn it off (`LOCAL_AI_EMBED_BACKEND=none`), because next to a 35B model it costs speed.
2. **Two server lists:**

   | File | For | Servers |
   |---|---|---|
   | `%USERPROFILE%\.lmstudio\mcp.json` | LM Studio chat & `local-coder` (Goose) | [config/lmstudio/mcp.json.example](config/lmstudio/mcp.json.example): `library`, `compute`, `web`, `notebook`, `coder` |
   | `%USERPROFILE%\local-ai-workspace\mcp.json` (`LOCAL_AI_MCP_CONFIG`) | Research & code agents (`local-ai`, `local-ai ui`) | [config/agent/mcp.json.example](config/agent/mcp.json.example): `library`, `compute`, `notebook`, `web` |

   Edit the LM Studio list in LM Studio (Program → Install → **Edit mcp.json**). Copy the agent list into the
   workspace. Change paths if your folders differ.
   `web` ([duckduckgo-mcp-server](https://pypi.org/project/duckduckgo-mcp-server/)) is optional and needs internet.

3. **System prompt** for the chat: [config/lmstudio/system-prompt.md](config/lmstudio/system-prompt.md). Save it
   as a preset.
4. **Offline data:** put Kiwix ZIM archives (Wikipedia, DevDocs) into `LOCAL_AI_ZIM_DIR` (e.g.
   `C:\Users\you\wiki-zim`) and your PDFs into `LOCAL_AI_LIBRARY` (e.g. `C:\Users\you\physics-library`). See
   [docs/offline-data.md](docs/offline-data.md).
5. **Index** (optional): `.venv\Scripts\local-ai-index.exe` indexes the library at once (~20 s for 560 files;
   see `--help` for Zotero, BibTeX and arXiv metadata).
6. Restart LM Studio so it picks up the servers.

<details>
<summary><b>Setup on Linux</b></summary>

```bash
git clone https://github.com/salihyesil59/local-ai ~/local-ai && cd ~/local-ai
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
```

Commands are in `.venv/bin/` (no `.exe`). LM Studio's MCP config is `~/.lmstudio/mcp.json`; the agent's is
`~/local-ai-workspace/mcp.json` (`export LOCAL_AI_MCP_CONFIG=~/local-ai-workspace/mcp.json`). In path lists
(`LOCAL_AI_ZIM_DIR`, `LOCAL_AI_SKILLS`) use `:` instead of `;`. Everything else is the same as on Windows.

</details>

## Research agent

```powershell
local-ai "Kara cisim ışımasında Wien yasası nasıl türetilir?" -v --out report.md
local-ai --quick "Hidrojenin taban durum enerjisi kaç eV?"   # fast answer, no report (~1 min)
local-ai ui                                  # web interface on http://127.0.0.1:8765
local-ai export <project> -f html -f docx   # html, md, bib, tex, docx, pdf
local-ai data status                        # download / update ZIM archives and arXiv data
```

These assume the environment is activated; otherwise prefix with `.venv\Scripts\`. Set
`$env:LOCAL_AI_MCP_CONFIG = "$HOME\local-ai-workspace\mcp.json"` so the agent uses its own server list.
LM Studio's server must be running (Developer tab → **Start Server**).

How a run works and all options: [docs/agent.md](docs/agent.md).

## Code agent

The code agent (`local-coder`) reads source files, writes and edits code, runs tests, and self-verifies.
It is a superset of the research agent in Code mode: it can also call `library`, `compute`, and `web` tools —
so you can hand it an arXiv link and it will download the paper, understand the algorithm, implement it, and
test it.

### Web UI (recommended)

```powershell
local-ai ui   # opens http://127.0.0.1:8765
```

Switch to the **Code** tab. Select which MCP servers the run should use with the chip selectors (similar to
Goose's per-session extension toggles):

- **library** — search your PDFs, Wikipedia, arXiv
- **compute** — run Python, check units
- **web** — DuckDuckGo search (needs internet)

Click **Compact** when the context grows large; the agent summarises dropped context automatically and keeps
the plan and all written files.

### CLI

```powershell
local-coder run "implement the Adam optimiser from scratch and add unit tests"   # in the current folder
local-coder run --project C:\my-project "fix the failing tests in src/solver.py"
local-coder check C:\my-project    # exit code 0 only when the plan is finished and the tests pass
```

### As an MCP server (Goose / LM Studio)

Without a subcommand `local-coder` is an MCP stdio server (`--files` adds file tools for clients that have
none) — add it to LM Studio or Goose exactly like the other servers. See [config/lmstudio/mcp.json.example](config/lmstudio/mcp.json.example) and
[docs/goose.md](docs/goose.md).

### Code skills

Built-in skills teach the agent common patterns. They appear in the **Skills** panel of the web UI with a
`[code]` badge and can be referenced by name in any prompt.

| Skill | What it does |
|---|---|
| `bug-fix` | Reproduce the bug first, then fix, then add a regression test |
| `new-feature` | Plan → scaffold → implement → test → document |
| `from-paper` | Download an arXiv paper, understand the algorithm, implement and test it |
| `scientific-code` | Implement physics/math algorithms with unit and numerical checks |
| `edge-cases` | Enumerate and test edge cases for existing code |
| `refactor` | Improve code structure without changing behaviour |
| `cli-tool` | Build a command-line tool with argparse / click |

### arXiv → code workflow

Give the agent a paper URL and it will do the full pipeline:

```
local-ai ui  →  Code tab  →  enable library + compute
Prompt: "https://arxiv.org/abs/2301.13379 makalesindeki algoritmayı Python'da uygula ve test et"
```

The agent will:
1. Download and read the paper via `library__read_arxiv`
2. Extract the core algorithm
3. Implement it in the workspace
4. Run tests via `compute__python`
5. Fix failures and iterate

## MCP server selection (UI)

The web UI lets you pick which MCP servers are active for each run — independently for Research and Code
tabs. Choices are saved per tab in the browser. This mirrors Goose's per-session extension toggles.

## Configuration

All servers and agents read the same environment variables (set them in `mcp.json` → `env`).
`%USERPROFILE%` is `C:\Users\<you>` on Windows; on Linux the defaults are under `~`.

| Variable | Default | Used for |
|---|---|---|
| `LOCAL_AI_WORKSPACE` | `%USERPROFILE%\local-ai-workspace` | notes, reports, memory, indexes, caches |
| `LOCAL_AI_LIBRARY` | `%USERPROFILE%\physics-library` | your PDF / .md / .txt files, indexed automatically |
| `LOCAL_AI_ZIM_DIR` | `%USERPROFILE%\wiki-zim` | ZIM folders and/or files, separated by `;` (Linux: `:`) |
| `LOCAL_AI_BASE_URL` | `http://localhost:1234/v1` | LM Studio's OpenAI-compatible server |
| `LOCAL_AI_API_KEY` | `lm-studio` | API key sent to that server |
| `LOCAL_AI_MODEL` | first loaded LLM | chat model for the agent |
| `LOCAL_AI_MCP_CONFIG` | `%USERPROFILE%\.lmstudio\mcp.json` | server list for the agent (`local-ai`, `local-ai ui`) |
| `LOCAL_AI_EMBED_MODEL` | `text-embedding-qwen3-embedding-0.6b` | embedding model for `library` and memory |
| `LOCAL_AI_EMBED_QUERY_TASK` | retrieval instruction for Qwen3-Embedding | instruction put before queries; empty turns it off |
| `LOCAL_AI_EMBED_BACKEND` | `lmstudio` | `none` for keyword search only, `hash` in tests |
| `LOCAL_AI_RERANK_MODEL` | | optional cross-encoder for `library` (extra `[rerank]`) |
| `LOCAL_AI_SKILLS` | | extra skill folders, separated like `LOCAL_AI_ZIM_DIR` |

Workspace layout:

```
library.sqlite          search index: chunks, BM25 (FTS5) index and (optional) embedding vectors
memory.sqlite           long-term memory
figures\  compute\  skills\
cache\arxiv\            downloaded arXiv PDFs
arxiv\metadata.jsonl    harvested arXiv metadata
<project>\              notes\, sources.json, report.md, computations.ipynb, exports\
```

## Goose integration

All servers are stdio MCP servers and work as Goose extensions. See [docs/goose.md](docs/goose.md) and
[config/goose/config.example.yaml](config/goose/config.example.yaml) for a ready-made Goose config.

Recommended extension setup for coding:

- **Always on:** `developer`, `analyze`, `todo`, `skills`, `docs` (`local-ai-docs`)
- **Toggle per session:** `library`, `web` — turn on when you need arXiv / Wikipedia / your library or web search
- **`coder`** (`local-coder`) — add as a Command-line Extension for full file-write + test-run capabilities inside Goose

## Security

`compute`'s `python` and `coder`'s `verify` execute model-written code on your machine; there is no
sandbox. LM Studio asks before every tool call: always-allow the read-only tools (`search_*`, `read_*`,
`wikipedia`, `docs`), but keep confirmations for code execution and file writes.

## Development

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest                  # real servers + scripted model, no LM Studio needed
.venv\Scripts\ruff check; .venv\Scripts\ruff format --check
```

On Linux use `.venv/bin/` instead of `.venv\Scripts\`.

## Layout

```
src/local_ai/
  core/             shared by both agents: model connection (llm), MCP bridge, tools, skills, text helpers
  research/         research agent: CLI, loop, prompts, checks, data downloads
  coder/            coding agent: CLI, loop, prompts, plan/verify workflow, file I/O, skills
  servers/          MCP servers: library (+ zim, arxiv, importers), compute, notebook (+ memory, skills), docs,
                    coder; common.py (settings, embeddings), bibtex.py, export.py
  web/              web UI for both agents: app (endpoints), runs, store, static files
  skills/           built-in research skills
config/
  lmstudio/         mcp.json templates (Windows, Linux), system prompt, model settings
  agent/            mcp.json templates for the research agent
  goose/            config.yaml template and .goosehints for coding with the local model
docs/               servers, agent, offline data, Goose
tests/              pytest suite (research + coder tests)
```

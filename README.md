# local-ai

A research assistant that runs on your own computer: a local model in LM Studio (built for
[`salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF`](https://huggingface.co/salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF))
plus MCP tool servers that search your PDFs, offline Wikipedia and documentation, **compute and unit-check instead
of guessing**, remember what they learned and write cited reports that are checked automatically.

Three ways to use it, sharing the same tools:

| | LM Studio chat | `local-ai` CLI | `local-ai ui` (browser) |
|---|---|---|---|
| Best for | interactive questions, follow-ups | deep multi-step research, scripting | deep research with a live view, browsing past projects |
| Loop | the model calls tools inside the chat | recall → plan → research → gap check → write → review → verify → learn | same as CLI |
| Checks | prompt rules + self-check | automatic: numbers must appear in tool output, citations must exist, quotes must match sources, plus a reviewer pass | same as CLI |
| Output | chat answer | report.md, exports, computations.ipynb | same, plus export buttons |

The same servers also work in [Goose](docs/goose.md) for coding.

## Servers

| Command | Server | Tools | Needs |
|---|---|---|---|
| Command | Server | Tools | For |
|---|---|---|---|
| `local-ai-library` | `library` | `search_library`, `read_library`, `wikipedia`, `docs`, `search_arxiv`, `read_arxiv` | looking things up: your library (indexed automatically), offline Wikipedia and docs, arXiv |
| `local-ai-compute` | `compute` | `python` (persistent kernel), `check_units`, `solve`, `reset`, `export_notebook` | calculations you can check |
| `local-ai-notebook` | `notebook` | notes, sources, reports, exports, memory, skills | the research agent |
| `local-ai-docs` | `docs` | `docs` only | coding agents (Goose) |

Each job is done by exactly one tool. Details: [docs/servers.md](docs/servers.md). The LM Studio chat uses
`library` + `compute` (+ the optional `web` search server): 13 tools, no embedding model, no index step.

## Setup (Windows)

Requirements: Python 3.10+ (3.13 recommended), [LM Studio](https://lmstudio.ai) and, for the optional `web`
server, [uv](https://docs.astral.sh/uv/). All commands are for PowerShell.

```powershell
cd $HOME\Github\ai
git clone https://github.com/salihyesil59/local-ai
cd local-ai
py -3.13 -m venv .venv
.venv\Scripts\pip install -e ".[all]"
```

Use `".[dev]"` to also run the tests and the linter. The commands are
installed in `.venv\Scripts\` (`local-ai.exe`, `local-ai-library.exe`, ...). Either call them with that path or
activate the environment first with `.venv\Scripts\Activate.ps1`. If PowerShell blocks the script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

1. **LM Studio:** load the chat model with the settings in [config/lmstudio/preset.md](config/lmstudio/preset.md).
   Keyword search needs nothing else. Semantic ranking also needs the embedding model **Qwen3-Embedding-0.6B**;
   the example configs turn it off (`LOCAL_AI_EMBED_BACKEND=none`), because next to a 35B model it costs speed.
2. **Two server lists:**

   | File | For | Servers |
   |---|---|---|
   | `%USERPROFILE%\.lmstudio\mcp.json` | LM Studio chat | [config/lmstudio/mcp.json.example](config/lmstudio/mcp.json.example): `library`, `compute`, `web` |
   | `%USERPROFILE%\local-ai-workspace\mcp.json` (`LOCAL_AI_MCP_CONFIG`) | research agent (`local-ai`, `local-ai ui`) | [config/agent/mcp.json.example](config/agent/mcp.json.example): `library`, `compute`, `notebook` |

   Edit the first one in LM Studio (Program → Install → **Edit mcp.json**). Copy the second one into the
   workspace. Change the paths if your folders differ. The chat list is kept short on purpose: every tool
   definition goes into every prompt, and 30+ tools slow a local model down and confuse its tool choice; the
   notebook tools are only needed by the agent.
   `web` ([duckduckgo-mcp-server](https://pypi.org/project/duckduckgo-mcp-server/)) is optional and needs internet.
3. **System prompt** for the chat: [config/lmstudio/system-prompt.md](config/lmstudio/system-prompt.md). Save it
   as a preset.
4. **Offline data:** put Kiwix ZIM archives (Wikipedia, DevDocs) into `LOCAL_AI_ZIM_DIR` (e.g.
   `C:\Users\you\wiki-zim`) and your PDFs into `LOCAL_AI_LIBRARY` (e.g. `C:\Users\you\physics-library`). See
   [docs/offline-data.md](docs/offline-data.md).
5. **Index** (optional): the library is indexed on the first searches; `.venv\Scripts\local-ai-index.exe` does it
   at once (about 20 s for 560 files; see `--help` for Zotero, BibTeX and arXiv metadata).
6. Restart LM Studio so it starts the servers.

Then ask in any language, e.g. *"Hidrojen atomunun taban durum enerjisini türet ve birimlerini kontrol et."*

<details>
<summary><b>Setup on Linux</b></summary>

```bash
git clone https://github.com/salihyesil59/local-ai ~/local-ai && cd ~/local-ai
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
```

The commands are in `.venv/bin/` (no `.exe`). LM Studio's MCP config is `~/.lmstudio/mcp.json`: start from
[config/lmstudio/mcp.linux.json.example](config/lmstudio/mcp.linux.json.example); the agent's is
[config/agent/mcp.linux.json.example](config/agent/mcp.linux.json.example), copied to
`~/local-ai-workspace/mcp.json` with `export LOCAL_AI_MCP_CONFIG=~/local-ai-workspace/mcp.json`. Index with
`.venv/bin/local-ai-index ~/papers`. In paths lists (`LOCAL_AI_ZIM_DIR`, `LOCAL_AI_SKILLS`) the separator is `:`
instead of `;`. Everything else is the same as on Windows.

</details>

## Research agent

```powershell
local-ai "Kara cisim ışımasında Wien yasası nasıl türetilir?" -v --out report.md
local-ai --quick "Hidrojenin taban durum enerjisi kaç eV?"   # fast answer, no report (~1 min)
local-ai ui                                 # web interface on http://127.0.0.1:8765
local-ai export <project> -f html -f docx   # html, md, bib, tex, docx, pdf
local-ai data status                        # download / update ZIM archives and arXiv data
```

These assume the environment is activated; otherwise use `.venv\Scripts\local-ai.exe` (`.venv/bin/local-ai` on
Linux). Set `$env:LOCAL_AI_MCP_CONFIG = "$HOME\local-ai-workspace\mcp.json"` so the agent uses its own server list
(without it, it reads LM Studio's `mcp.json`); the agent needs LM Studio's server running (Developer tab →
**Start Server**). How a run works and all options: [docs/agent.md](docs/agent.md).

## Configuration

All servers and the agent read the same environment variables (set them in `mcp.json` → `env`). `%USERPROFILE%` is
`C:\Users\<you>` on Windows; on Linux the defaults are under `~` instead.

| Variable | Default | Used for |
|---|---|---|
| `LOCAL_AI_WORKSPACE` | `%USERPROFILE%\local-ai-workspace` | notes, reports, memory, indexes, caches |
| `LOCAL_AI_LIBRARY` | `%USERPROFILE%\physics-library` | your PDF / .md / .txt files, indexed automatically |
| `LOCAL_AI_ZIM_DIR` | `%USERPROFILE%\wiki-zim` | ZIM folders and/or files, separated by `;` (Linux: `:`) |
| `LOCAL_AI_BASE_URL` | `http://localhost:1234/v1` | LM Studio's OpenAI-compatible server |
| `LOCAL_AI_API_KEY` | `lm-studio` | API key sent to that server |
| `LOCAL_AI_MODEL` | first loaded LLM | chat model for the agent |
| `LOCAL_AI_MCP_CONFIG` | `%USERPROFILE%\.lmstudio\mcp.json` | server list for the agent (`local-ai`, `local-ai ui`), e.g. a fuller set than the LM Studio chat uses |
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

## Security

`compute`'s `python` executes model-written code on your machine; there is no sandbox. LM Studio asks before
every tool call: you can always-allow the read-only tools (`search_*`, `read_*`, `wikipedia`, `docs`), but keep the
confirmation for code execution.

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
  agent/            research agent: CLI, loop, prompts, MCP bridge, web UI, data downloads
  servers/          MCP servers: library (+ zim, arxiv, importers), compute, notebook (+ memory, skills), docs;
                    common.py (settings, embeddings), bibtex.py, export.py
  skills/           built-in research skills
config/
  lmstudio/         chat mcp.json templates (Windows, Linux), system prompt, recommended model settings
  agent/            mcp.json templates for the research agent
  goose/            .goosehints for coding with the local model
docs/               servers, agent, offline data, Goose
tests/              pytest suite
```

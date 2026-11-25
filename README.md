# local-ai

A local research assistant that runs entirely on your own computer: a local model served by an OpenAI-compatible
server such as [Unsloth Studio](https://unsloth.ai/docs/new/studio) (built for
[`salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF`](https://huggingface.co/salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF)),
with a **research agent** that searches your PDFs, offline Wikipedia and arXiv, computes and unit-checks, and
writes cited reports, and a plain **chat** that can use the same MCP tool servers.

## Modes

| | Chat | Research agent | `local-ai ui` (browser) |
|---|---|---|---|
| Best for | Quick questions, with the tools you switch on | Deep multi-step research | Both, with a live view |
| Trigger | `local-ai ui` → Chat tab | `local-ai` CLI | `local-ai ui` → Research tab |
| Output | Chat answer | `report.md`, exports, notebook | Same, plus export buttons |

## Servers

| Command | Server | Tools | For |
|---|---|---|---|
| `local-ai-library` | `library` | `search_library`, `read_library`, `wikipedia`, `docs`, `search_arxiv`, `read_arxiv`, `read_pdf` | Your PDFs, offline Wikipedia & docs, arXiv |
| `local-ai-compute` | `compute` | `python` (persistent kernel), `check_units`, `solve`, `reset`, `export_notebook` | Calculations you can verify |
| `local-ai-notebook` | `notebook` | notes, sources, reports, exports, memory, skills | Research agent internals |
| `local-ai-documents` | `documents` | `make_pdf`, `make_docx` | An answer as a PDF or Word file |

Details: [docs/servers.md](docs/servers.md).

## Setup (Windows)

Requirements: Python 3.10+ (3.13 recommended) and an OpenAI-compatible model server, e.g.
[Unsloth Studio](https://unsloth.ai/docs/new/studio). For the optional `web` server,
[uv](https://docs.astral.sh/uv/) is also needed. All commands are for PowerShell.

```powershell
cd $HOME\Github\ai
git clone https://github.com/salihyesil59/local-ai
cd local-ai
py -3.13 -m venv .venv
.venv\Scripts\pip install -e ".[all]"
```

Use `".[dev]"` to also install test and lint tools. Commands are installed in `.venv\Scripts\`
(`local-ai.exe`, `local-ai-library.exe`, …). Either call them with the full path or activate the environment
first with `.venv\Scripts\Activate.ps1`. If PowerShell blocks scripts, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

1. **Model server:** load the chat model and start the API (see [Unsloth Studio](#unsloth-studio) below). Set
   `LOCAL_AI_BASE_URL` to the URL it prints and `LOCAL_AI_API_KEY` to its API key.
   Keyword search needs nothing else. Semantic ranking also needs an embedding model such as
   **Qwen3-Embedding-0.6B** on the server; the example config turns it off (`LOCAL_AI_EMBED_BACKEND=none`),
   because next to a 35B model it costs speed.
2. **Server list:** copy [config/mcp.json.example](config/mcp.json.example) (Linux:
   [config/mcp.linux.json.example](config/mcp.linux.json.example)) to `%USERPROFILE%\.local-ai\mcp.json` and
   change the paths if your folders differ. It holds `library`, `compute`, `notebook`, `documents` and `web`.
   Another place works too: set `LOCAL_AI_MCP_CONFIG` to it. A `%USERPROFILE%\.lmstudio\mcp.json` of earlier
   versions is still read while `.local-ai\mcp.json` does not exist.
   `web` ([duckduckgo-mcp-server](https://pypi.org/project/duckduckgo-mcp-server/)) is optional and needs internet.
3. **Offline data:** put Kiwix ZIM archives (Wikipedia, DevDocs) into `LOCAL_AI_ZIM_DIR` (e.g.
   `C:\Users\you\wiki-zim`) and your PDFs into `LOCAL_AI_LIBRARY` (e.g. `C:\Users\you\local-ai-library`). See
   [docs/offline-data.md](docs/offline-data.md).
4. **Index** (optional): `.venv\Scripts\local-ai-index.exe` indexes the library at once (~20 s for 560 files;
   see `--help` for Zotero, BibTeX and arXiv metadata).

[config/system-prompt.md](config/system-prompt.md) is a system prompt for using the tool servers from another
chat program.

<details>
<summary><b>Setup on Linux</b></summary>

```bash
git clone https://github.com/salihyesil59/local-ai ~/local-ai && cd ~/local-ai
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
```

Commands are in `.venv/bin/` (no `.exe`). The server list is `~/.local-ai/mcp.json`. In path lists
(`LOCAL_AI_ZIM_DIR`, `LOCAL_AI_SKILLS`) use `:` instead of `;`. Everything else is the same as on Windows.

</details>

## Unsloth Studio

Any server with OpenAI-style `/v1/chat/completions` (streamed, with tool calls) and `/v1/models` works; these are
the steps for Unsloth Studio.

1. Load the chat model in Studio (or start it with `unsloth run --model …`). Give it a context of 65536–131072
   tokens (`-c 131072` with `unsloth run`; llama-server flags are passed on): tool outputs and notes add up quickly.
2. Create an API key: the avatar at the bottom left → Settings → API → Create. It starts with `sk-unsloth-` and
   is shown once. Every request needs it.
3. Point local-ai at the server, with the URL Studio prints (often port 8888):

   ```powershell
   $env:LOCAL_AI_BASE_URL = "http://localhost:8888/v1"
   $env:LOCAL_AI_API_KEY = "sk-unsloth-..."
   ```

   The MCP servers get the same variables from the environment; put them in `mcp.json` → `env` only when they
   differ there.
4. For semantic search, set up an embedding model (Qwen3-Embedding-0.6B) in Studio. local-ai asks for the model
   `default`, Studio's own embedding model; set `LOCAL_AI_EMBED_MODEL` to an id from `/v1/models` to choose
   another one, and `LOCAL_AI_EMBED_BASE_URL` when embeddings come from a second server. After the embedding
   model changes, run `local-ai-index --reembed` (until then search falls back to keywords).
5. Studio's own server-side tools (python, terminal, web search) are not needed: local-ai brings its tools as
   MCP servers. With `unsloth run`, `--disable-tools` keeps them off.

Sampling (temperature, top_p, top_k) is sent with every request. The model's thinking may come in
`reasoning_content` or inline as `<think>…</think>`; both are handled. Tool calls through Studio need a recent
version (fixed in spring 2026); if the model says it has no tools, update Studio. The web UI runs up to four
chats at once: give the server as many parallel slots (`--parallel 4`), or the extra chats wait.

## Research agent

```powershell
local-ai "Kara cisim ışımasında Wien yasası nasıl türetilir?" -v --out report.md
local-ai --quick "Hidrojenin taban durum enerjisi kaç eV?"   # fast answer, no report (~1 min)
local-ai ui                                  # web interface on http://127.0.0.1:8765
local-ai export <project> -f html -f docx   # html, md, bib, tex, docx, pdf
local-ai data status                        # download / update ZIM archives and arXiv data
```

These assume the environment is activated; otherwise prefix with `.venv\Scripts\`. The model server must be
running.

How a run works and all options: [docs/agent.md](docs/agent.md).

## MCP server selection (UI)

The web UI lets you pick which MCP servers are active for each message, independently for the Chat and Research
tabs. Choices are saved per tab in the browser.

## Configuration

All servers and agents read the same environment variables (set them in your environment or in `mcp.json` →
`env`). `%USERPROFILE%` is `C:\Users\<you>` on Windows; on Linux the defaults are under `~`.

| Variable | Default | Used for |
|---|---|---|
| `LOCAL_AI_WORKSPACE` | `%USERPROFILE%\local-ai-workspace` | notes, reports, memory, indexes, caches |
| `LOCAL_AI_LIBRARY` | `%USERPROFILE%\local-ai-library` | your PDF / .md / .txt files, indexed automatically; `notebooks\` holds the notebooks |
| `LOCAL_AI_ZIM_DIR` | `%USERPROFILE%\wiki-zim` | ZIM folders and/or files, separated by `;` (Linux: `:`) |
| `LOCAL_AI_BASE_URL` | `http://localhost:8888/v1` | the model server's OpenAI-compatible API |
| `LOCAL_AI_API_KEY` | `local` | API key sent to that server (Unsloth Studio: `sk-unsloth-…`) |
| `LOCAL_AI_MODEL` | first loaded LLM | chat model for the agent |
| `LOCAL_AI_MCP_CONFIG` | `%USERPROFILE%\.local-ai\mcp.json` | server list for the agent (`local-ai`, `local-ai ui`) |
| `LOCAL_AI_EMBED_MODEL` | `default` | embedding model for `library` and memory |
| `LOCAL_AI_EMBED_BASE_URL` | `LOCAL_AI_BASE_URL` | server of the embedding model, when it is another one |
| `LOCAL_AI_EMBED_QUERY_TASK` | retrieval instruction for Qwen3-Embedding | instruction put before queries; empty turns it off |
| `LOCAL_AI_EMBED_BACKEND` | `server` | `none` for keyword search only, `hash` in tests |
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

`compute`'s `python` executes model-written code on your machine; there is no sandbox. Switch `compute` on only
for messages that need it.

## Development

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest                  # real servers + scripted model, no model server needed
.venv\Scripts\ruff check; .venv\Scripts\ruff format --check
```

On Linux use `.venv/bin/` instead of `.venv\Scripts\`.

## Layout

```
src/local_ai/
  core/             model connection (llm), MCP bridge, tools, skills, text helpers, attachments
  chat.py           chat mode
  research/         research agent: CLI, loop, prompts, checks, data downloads
  servers/          MCP servers: library (+ zim, arxiv, importers), compute, notebook (+ memory, skills),
                    documents; common.py (settings, embeddings), bibtex.py, export.py
  web/              web UI: app (endpoints), runs, store, offline view, static files
  skills/           built-in research skills
config/             mcp.json templates (Windows, Linux), system prompt
docs/               servers, agent, offline data
tests/              pytest suite
```

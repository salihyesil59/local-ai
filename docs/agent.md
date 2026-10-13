# Research agent

`local-ai` runs deep, multi-step research with the model loaded in LM Studio and the MCP servers listed in
`LOCAL_AI_MCP_CONFIG` (template: [config/agent/mcp.json.example](../config/agent/mcp.json.example)), else LM
Studio's `mcp.json`, or `--mcp-config path`. It needs the `notebook` server. Start
LM Studio's server first (Developer tab → **Start Server**). The examples assume the environment is activated
(`.venv\Scripts\Activate.ps1`, on Linux `source .venv/bin/activate`).

```powershell
local-ai "What limits the efficiency of perovskite solar cells?" -v --out report.md
local-ai "Kara cisim ışımasında Wien yasası nasıl türetilir?" -v
local-ai --resume --project "kara-cisim-isimasinda-wien-yasasi-nasil-turetilir"
```

| Option | Default | |
|---|---|---|
| `--model` | first loaded LLM | model id as listed by LM Studio (also `LOCAL_AI_MODEL`) |
| `--base-url` | `http://localhost:1234/v1` | also `LOCAL_AI_BASE_URL` |
| `--max-steps` | 12 | tool calls per sub-question |
| `--max-tool-chars` | 6000 | tool output kept in context (head + tail) |
| `--max-tokens` | 8192 | tokens per model reply, thinking included; stops a model stuck in a loop |
| `--report-tokens` | 16384 | the same limit for the replies that write and correct the report |
| `--servers` | all | e.g. `library,compute,notebook` |
| `--project` / `--resume` | | continue an interrupted run from its notes |
| `--parallel` | 1 | sub-questions researched at the same time (enable parallel requests in LM Studio) |
| `--no-critic`, `--no-gap-check`, `--no-learn`, `--fast` | all on | skip phases for speed |
| `--quick` | off | fast answer instead of a report (see below) |

## Quick answers

`local-ai --quick "..."` (or **Quick** in the web UI) skips the research pipeline:

- one tool-calling loop on the whole question, up to 6 tool calls (`quick_steps`);
- no plan, notes, project, report, review or learning;
- memory is still recalled;
- the notebook tools are hidden from the model;
- numbers that no tool output backs are flagged at the end, as in reports.

Use it for definitions, single calculations and short factual questions, and the full run for anything that needs
several sources or a cited write-up. **Fast** keeps the full pipeline but skips review, gap check and learning.

## Tool roles

Tools are exposed as `<server>__<tool>` and mapped to roles from their names, so any set of servers works. When
several servers offer a role, the first match in the preference order wins (e.g. `compute__python` before any
other `*__python` / `*__run_python`). With the package's servers every role has exactly one tool: search and read
from `library`, Wikipedia, docs and arXiv from `library`, calculations from `compute`.

## How a run works

1. **Recall**: relevant memories and preferences, plus the list of skills.
2. **Plan**: 2-6 sub-questions with search terms in the question's language *and* English; a skill per
   sub-question where one fits.
3. **Research**: a tool-calling loop per sub-question with a fresh context. The chosen skill is part of the
   instructions, and formulas must pass `check_units`. Findings and raw tool output are saved as notes.
4. **Gap check**: the model compares findings with the question and may add up to 2 sub-questions.
5. **Write**: the report, from the notes, in the question's language and the user's preferences.
6. **Review + verify**: a reviewer pass (unsupported claims, logic, units, missing caveats) plus deterministic
   checks:
   - every number must appear in some tool output;
   - every `[n]` must be a registered source;
   - every registered quote must appear in the source text.

   Problems go back to the model with tools; anything still unverified is flagged in the report.
7. **Save + learn**: `report.md` with References and `computations.ipynb` with every calculation; cited facts and
   lessons go to long-term memory.

Identical read-only tool calls (searches, reads) are shared within a run, also between parallel sub-questions.

## Summarizing a paper

Ask for it in a normal run: `local-ai "https://arxiv.org/abs/2401.01234 makalesini ayrıntılı özetle" --fast`.
The planner picks the `paper-summary` skill as the only sub-question. The skill reads the whole paper two pages
at a time and writes long, section-by-section findings, from which the report is written. It raises the limits
for that sub-question in its front matter (`max_steps: 30`, `max_tool_chars: 12000`, `max_tokens: 16384`), so a
paper of up to ~60 pages is read in full; this needs a context length of 65536 or more in LM Studio.

## Web UI

```powershell
local-ai ui            # opens http://127.0.0.1:8765 (local only)
```

Ask a question and watch everything as it happens: the plan, every tool call (expandable), findings, reviewer
issues and checks. The report appears with citation tooltips and export buttons (HTML, MD, BibTeX, TeX, DOCX,
PDF). The sidebar lists:

- past projects: report, sources with quotes, findings, computations notebook, resume;
- long-term memory, with search and forget;
- skills.

Model output is shown as it is written: Markdown, coloured code and LaTeX formulas (`$..$`, `$$..$$`, `\(..\)`,
`\[..\]`). The **⚙** button of the message box opens the settings of the chat: the model (from those LM Studio
has), temperature, max tokens, max turns, the context limit and the thinking limit (code) or max steps
(research). A code reply that only thinks is stopped at the thinking limit, and at once when the thinking repeats
itself; the model is then told to act. Empty fields use the defaults; the
settings are saved with the chat and remembered for the next new one. During a code run the box also shows the
turn, the speed in tokens per second and how full the context is; at 100% it is compacted, and **Compact** does it
earlier. The context limit is 300 000 characters (about 90k tokens), which needs a context length of 131072 in
LM Studio; with a smaller context length set a smaller limit, about 2 characters per token of it.

In a report, pointing at a citation `[n]` shows its source with the registered quote; a click keeps the card
open and shows the passage of the tool output the quote was taken from.

The top bar switches the language of the page (English / Turkish; what the agents write stays in their own
language) and the theme (system, light, dark).

### Tools: MCP servers

The **Tools** button of the message box chooses the MCP servers a message may use (a switch per server,
remembered per mode). The **Tools** view lists every server of the mcp.json the UI was started with: whether it
runs, its tools, the reason when it did not start. There you can switch a server off, restart it, remove it, or
add one you wrote or downloaded: give a name and the command that starts it (or its URL, for a server reached
over HTTP), with environment variables if it needs them, or paste the JSON from the server's README
(`{"mcpServers": {"name": {"command": ..., "args": [...]}}}`). The server starts at once, without a
restart of the UI, and is written to the mcp.json. A server is a program that runs on your computer with your
rights: add only what you trust. The notebook server cannot be removed, the research agent needs it; the list
cannot be changed while a run is under way.

Keyboard: **Ctrl+K** opens the command palette (commands and chats by name), **Ctrl+Enter** sends, **Alt+N**
starts a new chat, **Ctrl+,** opens the settings, **/** searches the chats, **Esc** closes what is open.

It works offline, with no CDN: the page's only third-party code is [KaTeX](https://katex.org) (MIT), bundled in
`web/static/vendor/katex`.

## Code mode

The same page also writes code: switch **Research** to **Code**, give the absolute path of a project folder and the
task. The coding agent (`local_ai.coder`, also `local-coder run`) plans, writes files and runs the tests
until they pass, and the page shows the plan with its steps, every turn (time, tokens, tool calls) and the model's
thinking live. Leave the task empty to continue the plan in the folder. Cancel stops it; Compact shortens the
conversation before the next reply (the model writes notes, the plan stays; it also happens by itself when the
context gets long). The agent loads built-in coding skills (bug fix, new feature, refactor, from a paper, edge
cases, CLI) and your own from `~/.local-coder/skills`.

Code mode uses the model, `temperature` and `top_p` of the UI; it needs none of the MCP servers, and can use
the ones selected with the MCP chips. One run at a time, research or code, because one model is loaded.

### Working in an existing repository

A project that does not fit into the context (a library with dozens of files) is not read from top to bottom.
From 12 source files on, the task comes with a map of the project (files, classes, functions; the files the
task's words point to first and in detail) and the `existing-code` skill. The agent then looks places up:

- `search(pattern, path, glob)`: where a name is defined and used, as `file:line: text`;
- `outline(path)`: what a file defines, with line numbers, to read only that part with `read_file`;
- `repo_map(path)`: a folder in more detail.

`verify(tests="tests/test_x.py")` runs only the tests of the step (pytest); the review at the end runs the
whole suite. When the plan is saved the tests run once: those that fail already, and that the plan does not
name, are listed and not counted against the agent. Work on a branch: the agent edits the files in place and
does not use git. Give it one bounded job per chat (a bug, a function, a module), not a rewrite of the library.

## Chats and folders

Every run is a chat in the sidebar and is saved in `<workspace>/ui/` (an index plus the events of each run), so
chats are still there after a restart. A chat that was running when the server stopped is shown as
*interrupted*. Titles default to the start of the prompt; the **⋯** of a chat renames it, moves it to a folder or
deletes it (not while it runs). **+ Folder** creates folders for your programs or topics; the **+** of a folder
makes it the target of the next chat (shown above the prompt box), **⋯** renames or deletes it (its chats stay).
A chat keeps its history: the box at the bottom sends a follow-up message in the open chat, and the agent runs
again with the earlier question and its answer. In a code chat the follow-up works on the same project folder (an
empty message continues the plan); a research follow-up gets a project of its own, so the first report stays.
A code chat keeps its whole conversation with the model (also over a restart of the UI): the next message goes
on from everything that was read, said and done. **Stop** only ends the reply that is being written; nothing is
forgotten, and your next message continues from there. The conversation gets shorter only by a compaction,
your own (**Compact**) or the automatic one at the context limit. A research run that is stopped keeps what it
found as well: its plan and findings are saved in the project. An empty message goes on with that research where
it stopped; a question is answered with the findings gathered until then. **+ New chat** starts a fresh one. The search field filters the chats by title and question, **Pin to the
top** in the menu of a chat keeps it above the others, and a chat can be dragged onto a folder (or onto the
*Chats* head to take it out). Names are edited in place: Enter saves, Esc cancels.

The panel on the right follows the run: in a code chat the plan with its steps, the last verify and the files
changed; in a research chat the sub-questions and the registered sources. File edits are shown as a diff and
verify as a test result (PASSED / FAILED). **Project files** in the panel of a code chat lists the files of the
project folder; a click shows a file.

While a code run works, the message box sends a message to the running agent (**Steer**): the model reads it
before its next reply, so the reply it is writing and its tool calls still finish first. A research run takes no
messages until it has ended.

## Export

```powershell
local-ai export "kara-cisim-isimasinda-wien-yasasi-nasil-turetilir" -f html -f bib -f docx
```

`html`, `md` and `bib` need nothing extra. `tex` and `docx` need [pandoc](https://pandoc.org); `pdf` also needs a
LaTeX engine (xelatex, lualatex, pdflatex or tectonic). Citations become real BibTeX citations in tex / docx / pdf.

Speed tips (parallel research, speculative decoding): [config/lmstudio/preset.md](../config/lmstudio/preset.md#speed).

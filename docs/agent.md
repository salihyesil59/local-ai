# Research agent

`local-ai` runs deep, multi-step research with the model loaded in LM Studio and the MCP servers listed in
`LOCAL_AI_MCP_CONFIG` (template: [config/agent/mcp.json.example](../config/agent/mcp.json.example)), else LM
Studio's `mcp.json`, or `--mcp-config path`. It needs the `notebook` server. Start
LM Studio's server first (Developer tab → **Start Server**). The examples assume the environment is activated
(`.venv\Scripts\Activate.ps1`, on Linux `source .venv/bin/activate`).

A research drafts first, then checks:

1. **Draft**: the model answers from what it knows, at once (as a chat would); the draft is shown right away.
2. **Plan**: 2-6 checks of the draft: the key claims, formulas, derivation steps and numbers that need a source or
   a calculation, what the draft marked as unsure, and what it does not answer well.
3. **Research**: each check is researched with the tools (read, register, cite, compute): confirmed, corrected or
   not verifiable.
4. **Report**: the draft revised with the findings: kept complete, corrected where a check contradicts it, every
   checked claim cited; then the usual checks of numbers, citations and quotes (and the reviewer).

So a question the model already knows well costs a few checks, not a whole research from nothing.

In the web UI the **Preset** of the **+** menu sets the research options at once: **Standard** (reviewer on, sources
required), **Quick answer** (one tool loop with sources), **Fast research** (fast, short thinking, no reviewer),
**Deep research** (clarify and approve the plan, depth 1) and **Document** (Fast research with the Word/PDF
document). Changing any option afterwards makes it **Custom**; the last choice is remembered.

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
| `--no-critic`, `--no-gap-check`, `--no-learn`, `--fast` | all on | skip phases for speed (**Reviewer** switch in the web UI) |
| `--long-report` | off | write the report section by section (see below; **Long report** in the web UI) |
| `--depth N` | 0 | deepening: after each sub-question the findings are reflected on and up to 2 follow-ups added, N levels down (at most 3 per level; **Depth** in the web UI) |
| `--review` | off | clarifying questions before planning; in the web UI (**Clarify and approve the plan**) also the plan, to change and approve before the research starts |
| `--short-thinking` | off | a reply thinks at most ~1700 tokens, then is asked to act from there (**Short thinking**) |
| `--document` | off | a chapter per sub-question, then the report and the sources as .docx / .pdf (see below) |
| `--quick` | off | fast answer instead of a report (see below) |

A source is registered only after the researcher read a part of it (library pages, the arXiv paper, the Wikipedia
article, documentation or a web page): `add_source` right after a search hit is refused with a request to read first
(a computation, origin `python`, is registered at once). When the offline sources are not enough, the researcher may
search the internet (`web__search`) and read pages (`web__fetch_content`) if the `web` server is configured.

## Long reports

With `--long-report` the planner asks for 5-8 sub-questions, and the report is not written in one reply:

- an outline first: a short answer, 3-8 sections in depth, limitations (and an appendix for code), each section
  with the sub-questions it is written from;
- then every section in its own reply, with the whole `--report-tokens` budget and only its findings;
- the checks and the reviewer go section by section; a section with problems is rewritten alone, so the rest of the
  report keeps its length.

It takes one model reply per section more (two with the reviewer on), one after the other.

## Documents (Word / PDF)

With `--document` (**Document** in the web UI) the run also builds a document as it goes:

- right after each check is researched, its findings (detailed and cited already) become a chapter of their own,
  kept as the note `chapter-NN`, so a resumed run keeps them;
- at the end the report becomes the synthesis, and the registered sources follow on a page of their own, numbered as
  the `[n]` in the text;
- `<project>/document.md`, then `exports/document.docx` with a table of contents, and `document.pdf` when a LaTeX
  engine (xelatex, lualatex, pdflatex or tectonic) is installed.

Word and PDF need [pandoc](https://pandoc.org) (on Windows: `winget install JohnMacFarlane.Pandoc`); formulas become
Word equations. Without it the run still ends with `document.md` and says what is missing. Combine it with
`--long-report` for the longest document.

## Quick answers

`local-ai --quick "..."` (or **Quick** in the web UI) skips the research pipeline:

- one tool-calling loop on the whole question, up to 6 tool calls (`quick_steps`);
- no plan, notes, project, report, review or learning;
- memory is still recalled;
- the notebook tools are hidden from the model;
- with **Sources** on (the default; `--no-sources` turns it off): each fact names its source, an answer given
  without any tool call or with numbers that no tool output shows is sent back once to be checked with the tools,
  and numbers that still have no tool output behind them are flagged at the end, as in reports;
- with **Sources** off: the model still uses the tools when they help (your library first) but need not cite them;
  nothing is sent back or flagged.

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

Ask a question and watch everything as it happens: the plan, every tool call (plain calls in a row fold into one
line, edits and verifications stay in sight), findings, reviewer issues and checks. The report appears with
citation tooltips and export buttons (HTML, MD, BibTeX, TeX, DOCX, PDF). Code blocks have a **Copy** button.

The page has three parts:

- the **sidebar** on the left: **New chat**, the pages (Projects: report, sources with quotes, findings,
  computations notebook, resume; Memory, with search and forget; Skills; Tools; Offline), and below them the chats
  with their folders and a search. Its foot shows whether LM Studio answers, the model and the number of tools,
  and switches the language and the theme. **Ctrl+B** hides it; on a phone it is a drawer opened from the top bar;
- the **chat** in the middle, with the message box at its foot. The title in the top bar opens the menu of the chat
  (rename, pin, move to a folder, delete);
- the **panel** on the right (**Ctrl+.** or the button of the top bar shows or hides it): what the chat works with
  (mode, model, folder or notebook, Sources, options, tools), how full the context is with the turn and the speed,
  and the progress: the plan and verifications of a code chat, the sub-questions and sources of a research, the
  history of the messages and the project files.

The message box has three modes: **Chat**, **Research** and **Code**. Chat is a plain conversation with the model,
like the chat of LM Studio: no plan, no agent loop and no tools of its own, follow-up messages in the same chat, and
images. It offers no MCP server until you switch one on in the **+** menu of the message box (remembered for chats);
then the model may call its tools. The settings of a chat are the model, temperature and max tokens; a reply thinks
as long as max tokens allows, and a conversation longer than the context limit drops its oldest messages.

A chat can always make a PDF or Word file of an answer: ask for it ("bunu PDF olarak ver"). The `documents` MCP
server (`local-ai-documents`, or `python -m local_ai.servers.documents.server`) offers `make_pdf` (with
`also_docx` for a Word copy too, "PDF ve Word olarak ver") and `make_docx`:
the model writes the content in Markdown (formulas in LaTeX become Word equations), the file goes to
`<workspace>/outputs/`, and the chat shows a download link. It needs pandoc, and for PDF a LaTeX engine.

Files go with a message in every mode the same way (**Attach files**, paste or drop): up to ten PDF, Word (.docx),
PowerPoint (.pptx), Excel (.xlsx), Markdown, text, CSV, JSON, LaTeX or code files of at most 50 MB each. The model gets
their text with the message: up to 40 000 characters in chat and code, 12 000 in a research (its question goes into
every step). What does not fit is cut, and the whole file is put into the library as the notebook `ekler-<chat id>`
(Word, PowerPoint and Excel as the text read from them): the model is told to read it there with `read_library` and
to search it with `search_library`. The files are kept with the chat in `<workspace>/ui/files/`; deleting the chat
deletes them and its notebook.

Images go with a message through **Attach files** in the **+** menu, or pasted (Ctrl+V) or dropped onto the box: up
to six PNG, JPEG, WebP or GIF images, large ones scaled down to 1600 px. A code chat gives them to the model with the
message (a screenshot of an error, a sketch of a layout); a research chat first has the model write down what they
show (text exactly, plots and tables described) and researches with that. The model needs vision: in LM Studio load
it with its `mmproj` file. The images are kept with the chat in `<workspace>/ui/images/`.

Model output is shown as it is written: Markdown, coloured code and LaTeX formulas (`$..$`, `$$..$$`, `\(..\)`,
`\[..\]`). The model button at the right of the message box opens the settings of the chat: the model (from those
LM Studio has), temperature, max tokens, max turns, the context limit and the thinking limit (code) or max steps
(research). A code reply that only thinks is stopped at the thinking limit, and at once when the thinking repeats
itself; the model is then told to act. Empty fields use the defaults; the
settings are saved with the chat and remembered for the next new one. During a run the box shows how full the
context is, and the panel the turn and the speed in tokens per second; at 100% it is compacted, and **Compact** does
it earlier. The context limit is 300 000 characters (about 90k tokens), which needs a context length of 131072 in
LM Studio; with a smaller context length set a smaller limit, about 2 characters per token of it.

In a report, pointing at a citation `[n]` shows its source with the registered quote; a click keeps the card
open and shows the passage of the tool output the quote was taken from.

The **+** menu of the message box holds everything else a message can take: images, the notebook, the options of
the mode (Fast, Quick, Sources) and the MCP servers it may use. What is set shows as chips above the text, each
removed with one click.

The foot of the sidebar switches the language of the page (English / Turkish; what the agents write stays in their
own language) and the theme (system, light, dark).

### Offline: notebooks, library and ZIM archives

The **Offline** view of the sidebar manages what the agents read without the internet:

- **Notebooks**: a notebook is a handful of files for one topic, kept in `notebooks/<name>/` of the library. Choose
  it in the **+** menu of the message box (Chat and Research): `search_library` then looks only into its
  files (they are indexed the moment it searches, so a file added a minute ago is found), and the model is told to
  answer from them and name file and page. Asked to look further ("search the whole library"), it passes
  `scope=""`. Less to search, less to read: the answer comes faster than from the whole library.
- **Library**: drop PDF, Markdown or text files to copy them into `LOCAL_AI_LIBRARY`; the list shows which files
  the search index has. Files can be deleted here.
- **ZIM archives**: the archives of `LOCAL_AI_ZIM_DIR`; **Check for updates** asks the Kiwix catalog for newer
  releases (needs the internet) and downloads the chosen ones, with progress and Cancel (a cancelled download goes
  on where it stopped next time). The old file is deleted after the new one is complete, unless you keep it.

The folders are those of the `library` server in the UI's mcp.json (its `env`), so the view and the agents see the
same files.

### Tools: MCP servers

The **+** menu of the message box chooses the MCP servers a message may use (a switch per server,
remembered per mode). The **Tools** view lists every server of the mcp.json the UI was started with: whether it
runs, its tools, the reason when it did not start. There you can switch a server off, restart it, remove it, or
add one you wrote or downloaded: give a name and the command that starts it (or its URL, for a server reached
over HTTP), with environment variables if it needs them, or paste the JSON from the server's README
(`{"mcpServers": {"name": {"command": ..., "args": [...]}}}`). The server starts at once, without a
restart of the UI, and is written to the mcp.json. A server is a program that runs on your computer with your
rights: add only what you trust. The notebook server cannot be removed, the research agent needs it; the list
cannot be changed while a run is under way.

Keyboard: **Ctrl+K** opens the command palette (commands and chats by name), **Ctrl+Enter** sends, **Alt+N**
starts a new chat, **Ctrl+,** opens the settings, **Ctrl+B** hides the sidebar, **Ctrl+.** the panel, **/** searches
the chats, **Esc** closes what is open.

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

Before a task is called done, a **judge** checks it. The coder's own tests only check what the coder understood, so
when it misread the task they agree with it. The judge does not share that view:

- the program takes the code examples of the task (the fenced blocks of an issue, `>>>` and `In [1]:` sessions
  included) and runs them on the changed project, in its environment;
- a fresh conversation, which sees only the task, the git diff and those outputs, answers SOLVED or NOT SOLVED.

When the answer is NOT SOLVED, the review opens again and the coder gets the reasons and the outputs. This happens
at most twice; after that the task ends as usual. The judge runs in normal speed only. It needs a git repository
(the diff), and when it cannot give a clear answer it does not hold the task up.

A run that keeps getting stuck starts again. After four replies that were cut off while thinking, or that went
in circles, the model writes short notes on the attempt (the files that matter, what was tried, what to do next).
The conversation is then replaced by the task, the plan, the diff so far and those notes. The stuck turns are left
out, since reading them again keeps the model stuck, and the files on disk keep every change. This happens at most
twice in a run.

Two boxes make a code run shorter:

- **Quick**, for a small mechanical change (rename a variable everywhere, fix one line): no plan, no verify per
  step and no review. The model changes the files, answers with what it changed, and the program runs the
  project's tests once at the end (the answer says whether they pass). Thinking is cut at 3000 characters, the
  run at 25 turns.
- **Fast**, for a real but small task: plan and verify as usual, but no review at the end and no project map with
  the task; thinking is cut at 5000 characters.

What the chat set in its settings (thinking limit, max turns) wins over these. In every mode
`replace_in_files(old, new, path, glob, word, regex)` changes a text in all files in one call, so a rename does not
need a read and an edit per file.

Code mode uses the model, `temperature` and `top_p` of the UI; it needs none of the MCP servers, and can use
the ones selected with the MCP chips. It can run beside a research or a chat (see below).

### Working in an existing repository

A project that does not fit into the context (a library with dozens of files) is not read from top to bottom.
From 12 source files on, the task comes with a map of the project (files, classes, functions; the files the
task's words point to first and in detail) and the `existing-code` skill. The agent then looks places up:

- `search(pattern, path, glob)`: where a name is defined and used, as `file:line: text`;
- `outline(path)`: what a file defines, with line numbers, to read only that part with `read_file`;
- `repo_map(path)`: a folder in more detail.

`run_python(code)` runs a short script in the project folder with the project's environment (60 s at most) and
returns what it prints, so the agent sees what the code does and need not work it out in its thinking. It decides
nothing: a task is done only through `verify`. The tests that were in a file before are kept: an edit that changes
or deletes one is refused the first time, with the advice to fix the code instead; made again (the task asks for
that behaviour to change), it goes through.

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

A chat takes messages while it works. A code run reads one before its next reply (**Steer**), so the reply it
is writing and its tool calls still finish first. A chat answers it when the current answer is done (between tool
calls it reads it at once), and a research researches it as a follow-up question when the current research is
done. Each such message has a **Send now** button: it stops what the chat is doing (what it has done stays, as
after Stop) and answers the message at once.

Chats work at the same time, in any mode: a research, a code run and a chat can run side by side, up to four (the
requests LM Studio serves at once by default; more wait). Two code runs in the same project folder do not: the
second waits for the first. Side by side, each is slower than alone, since they share the model; a research with
**Parallel** above 1 uses several of those requests itself.

## Export

```powershell
local-ai export "kara-cisim-isimasinda-wien-yasasi-nasil-turetilir" -f html -f bib -f docx
```

`html`, `md` and `bib` need nothing extra. `tex` and `docx` need [pandoc](https://pandoc.org); `pdf` also needs a
LaTeX engine (xelatex, lualatex, pdflatex or tectonic). Citations become real BibTeX citations in tex / docx / pdf.

Speed tips (parallel research, speculative decoding): [config/lmstudio/preset.md](../config/lmstudio/preset.md#speed).

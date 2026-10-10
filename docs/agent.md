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

It works offline, with no CDN or external assets.

## Export

```powershell
local-ai export "kara-cisim-isimasinda-wien-yasasi-nasil-turetilir" -f html -f bib -f docx
```

`html`, `md` and `bib` need nothing extra. `tex` and `docx` need [pandoc](https://pandoc.org); `pdf` also needs a
LaTeX engine (xelatex, lualatex, pdflatex or tectonic). Citations become real BibTeX citations in tex / docx / pdf.

Speed tips (parallel research, speculative decoding): [config/lmstudio/preset.md](../config/lmstudio/preset.md#speed).

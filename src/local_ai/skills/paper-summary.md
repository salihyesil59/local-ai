---
name: paper-summary
description: Detailed summary of one paper or document, read from the first page to the last
when_to_use: one specific paper, arXiv link/id or PDF is named ("summarize this paper", "makaleyi özetle"); plan it as the ONLY sub-question
max_steps: 30
max_tool_chars: 12000
max_tokens: 16384
---
1. Open the paper at page 1, two pages per call: read_arxiv (arxiv_id, page=1, pages=2) for an arXiv link or id,
   read_library (source=<file>, page=1, pages=2) for a library PDF. The first line shows the page count.
   Register the paper with add_source (quote: a short exact phrase from the abstract).
2. Read the WHOLE paper in order (page=3, 5, 7, ... with pages=2) up to the reference list. Do not skip sections,
   do not re-read pages, and do not search other sources unless the question asks for a comparison.
3. Only after the last page, write the findings. They are the summary's only input, so make them long and
   complete, section by section in the paper's order:
   - problem, motivation and what was known before;
   - setup and method: model, assumptions, derivation steps, key equations in LaTeX, data and parameters;
   - results: every main quantitative result copied exactly, with units and what it is compared to;
   - limitations and open questions stated by the authors; limitations you notice, marked as your assessment;
   - the main takeaways.
4. Give the page of every number and equation as (p. n). Keep quotes short and exact.
5. Recompute a key number with `python` only if the question asks for a check.

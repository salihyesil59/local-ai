---
name: paper-summary
description: Summarize one paper accurately — problem, method, results, limitations
when_to_use: a specific paper, arXiv id or PDF is named; "summarize this paper", "makaleyi özetle"
---
1. Locate the paper (arxiv by id/title, or the PDF via search_library) and register it with add_source.
2. Read the abstract, introduction (last paragraph: contributions), method overview, main results table/figure
   captions and conclusion — use read_library with the chunk id for context.
3. Extract: problem and motivation; method (key idea in 2-3 sentences, key equations); experimental setup;
   main quantitative results (copy numbers exactly, with the page); limitations stated by the authors;
   limitations you notice (mark clearly as your assessment).
4. Re-derive or recompute one key number with `python` if the paper gives enough information.
5. Keep quotes short and exact; every number must have its page/section.

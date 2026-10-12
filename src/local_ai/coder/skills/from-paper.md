---
name: from-paper
description: Implement an algorithm or formula from a paper or a research note and check it against the paper's own numbers
when_to_use: the task contains an algorithm, equations, pseudo-code or a "spec" taken from a paper, or says to implement what a paper describes
keywords: paper, makale, algorithm, algoritma, arxiv, pseudo-code, equation, denklem, arxiv.org, pdf
---
1. Read the paper before you plan. An arXiv link or id: `library__read_arxiv(arxiv_id, page=1, pages=2)`. A PDF file
   on this computer: `library__read_pdf(path, page=1, pages=2)` with its absolute path (a file name alone means a file
   in the project folder: put the project folder in front of it). The first line shows the page count.
   Always read two pages per call, so that no page is cut.
2. Pages 1-2 give the abstract and the outline. Then find the parts to implement with `query=` ("Algorithm",
   "pseudocode", "procedure", the name of the method) and read those pages and the pages that define their symbols.
   Read the pages with the worked examples, result tables and parameter values too: they become your tests.
   Skip related work, proofs and the reference list.
3. Collect from the paper: the algorithm steps, every equation with the meaning and unit of each symbol, parameters and
   their default values, and every worked example or result table with its numbers.
   Write what is unclear or missing in the text into your plan as an assumption; do not invent silently.
4. Plan: call `plan` with one step per algorithm component (a function per equation or procedure), then one for the
   full pipeline. Put what you collected into the plan, it is what stays when the conversation is shortened: each step
   names its equation or algorithm number and page.
   Criteria: each equation as a test with the paper's numbers, and each example or table row as an expected value with
   its tolerance written in the criterion (for floats use pytest.approx with the precision the paper prints). If the
   paper gives no numbers, use cases you can compute by hand and properties the method must have (a symmetry, a limit,
   a known special case).
5. Write each function so that its name and variables follow the paper's notation; put the equation number in a short
   comment. Prefer plain Python or numpy; add a dependency only if the paper requires it. Read a page again when you
   are unsure about a formula: do not write it from memory.
6. Test small cases you can compute by hand first (n = 1, 2, identity, symmetric input), then the paper's examples.
   If the paper's numbers do not come out, re-read the equation and check units, indices (0 or 1 based) and signs
   before suspecting the paper.
7. Verify: call `verify` to run the test suite and confirm every acceptance criterion passes.
8. If the text leaves a choice open (initialisation, tie breaking, stopping rule), choose the simplest, document it in
   the module docstring and tell the user in the summary, with the list of what you implemented (equation and
   algorithm numbers) and what you left out.

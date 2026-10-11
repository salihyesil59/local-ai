---
name: from-paper
description: Implement an algorithm or formula from a paper or a research note and check it against the paper's own numbers
when_to_use: the task contains an algorithm, equations, pseudo-code or a "spec" taken from a paper, or says to implement what a paper describes
keywords: paper, makale, algorithm, algoritma, arxiv, pseudo-code, equation, denklem, arxiv.org
---
1. If an arXiv URL or paper ID is provided, call `library__read_arxiv(arxiv_id, page=1)` to read the paper. You can also search for specific sections using `library__read_arxiv(arxiv_id, query="algorithm")` or `query="pseudocode"` to directly locate the algorithm, equations and numerical examples.
2. Collect from the paper: the algorithm steps, every equation with the meaning and unit of each symbol, parameters and their default values, and every worked example or result table with its numbers.
   Write what is unclear or missing in the text into your plan as an assumption; do not invent silently.
3. Plan: call `plan` with one step per algorithm component (a function per equation or procedure), then one for the full pipeline.
   Criteria: each equation as a test with the paper's numbers, and each example or table row as an expected value with its tolerance written in the criterion (for floats use pytest.approx with the precision the paper prints).
4. Write each function so that its name and variables follow the paper's notation; put the equation number in a short comment. Prefer plain Python or numpy; add a dependency only if the paper requires it.
5. Test small cases you can compute by hand first (n = 1, 2, identity, symmetric input), then the paper's examples.
   If the paper's numbers do not come out, re-read the equation and check units, indices (0 or 1 based) and signs before suspecting the paper.
6. Verify: call `verify` to run the test suite and confirm every acceptance criterion passes.
7. If the text leaves a choice open (initialisation, tie breaking, stopping rule), choose the simplest, document it in the module docstring and tell the user in the summary.

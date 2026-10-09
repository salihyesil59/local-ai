---
name: derivation
description: Derive a formula or result step by step and verify it symbolically
when_to_use: "derive", "show that", "where does this formula come from", "türet", "ispatla"
---
1. State the starting point explicitly: the physical laws/definitions you start from, with a citation for each
   (textbook PDF via search_library, or Wikipedia). List assumptions (e.g. non-relativistic, ideal gas, small angle).
2. Define every symbol once, with its unit.
3. Do each algebra/calculus step with the `solve` tool or sympy in `python` — never by hand. Show the sympy code.
4. After the final expression:
   - run `check_units` on it with the expected unit;
   - check limiting cases (e.g. v -> 0, large mass, angle -> 0) numerically or with sympy `limit`;
   - compare with the form in a source if one exists, and cite it.
5. Present: assumptions -> numbered steps (LaTeX) -> final result boxed -> checks performed -> where the
   assumptions break down.

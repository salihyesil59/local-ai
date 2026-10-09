---
name: dimensional-analysis
description: Check formulas by units, or guess the form of a law from the relevant quantities
when_to_use: verifying any formula; "what does X depend on"; sanity-checking a numeric answer; "birim kontrolü"
---
Checking a formula:
1. List each symbol with its SI unit (from a source if unsure).
2. Call `check_units(expression, variables, expected_unit)`. MISMATCH or INCONSISTENT means the formula or a
   symbol's unit is wrong — find which term before going on.
3. Arguments of exp/log/sin must be dimensionless; check them separately.

Finding the form of a law (Buckingham pi):
1. List the relevant quantities and their dimensions (M, L, T, Θ, I).
2. In `python`, build the dimension matrix with numpy/sympy and find its null space — each null-space vector is a
   dimensionless group.
3. State the result as "target = (product of powers) × f(dimensionless groups)" and say clearly that the
   dimensionless function/constant is not determined by dimensional analysis alone.

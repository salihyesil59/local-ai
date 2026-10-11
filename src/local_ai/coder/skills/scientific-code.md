---
name: scientific-code
description: Numerical or physics code (integrals, special cases, units, solvers) that must agree with known values
when_to_use: the task computes physical or mathematical quantities: distances, integrals, ODEs, root finding, formulas with units and constants
keywords: integral, numerical, sayısal, fizik, physics, kozmoloji, cosmology, simulation, simülasyon, orbit, yörünge, differential, ode, solver, quantum, kuantum, formül, hubble, newton, kepler, gravit, kütle
---
1. Put every constant (with its unit) at the top of the module as a named constant, and write the unit of every
   input and output in the docstring of the function. Never mix units inside a formula; convert at the edges.
2. Plan one step per physical quantity or numerical tool (integrator, root finder, the formulas that use them).
   Criteria: each reference value from the request with the tolerance it is given, and each closed-form limit.
3. Test closed-form limits FIRST, they need no reference table: a case with a known analytic result (a special
   parameter value, a small or large argument, a symmetry or an identity between two of your functions). Use
   them to prove the integrator before the real formulas rely on it.
4. Numerical integration: write one small function and reuse it. Use composite Gauss-Legendre or adaptive
   Simpson with a relative tolerance below what the request demands. Integrals to infinity: substitute a finite
   variable (for example u = 1/(1+x)) instead of cutting the range at a large number. Check the result against
   a case with an analytic integral.
5. Branches (sign of a parameter, sinh against sin, a limit where a term vanishes): one test per branch and one
   on the boundary between two branches, with a tolerance that decides the boundary.
6. Compare floats with pytest.approx(value, rel=...), never with ==. Take the tolerance from the request; use a
   relative tolerance for large quantities.
7. Inverse problems (find x such that f(x) = y): bisection on a bracket you check first; say in the docstring
   which assumption (monotonic) it needs, and test that a round trip f(inverse(y)) gives back y.
8. Invalid input (negative mass, zero denominator, out-of-range value): raise the exception type the request
   names, with a message that says which value was wrong. Test each.

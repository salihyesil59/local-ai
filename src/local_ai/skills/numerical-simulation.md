---
name: numerical-simulation
description: Solve a problem numerically (ODE/PDE, integration, optimization) and validate the result
when_to_use: no closed form; "simulate", "plot", "numerically solve", "sayısal çözüm", "grafik çiz"
---
1. Write the model equations and parameters (with units and sources) in notes before coding.
2. Build it step by step in the persistent `python` kernel: parameters -> functions -> solver
   (scipy.integrate.solve_ivp, quad, optimize). Use SI units internally; pint for conversions.
3. Validate: compare against an analytic special case or limit; halve the step size / tighten tolerances and
   check the result changes less than the precision you report; check conservation laws where they apply.
4. Plot the key quantities with labeled axes and units (plt), and report the saved figure path.
5. Report numbers with justified significant figures, the validation done, and the parameter ranges where the
   model is trustworthy.

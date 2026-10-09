---
name: fermi-estimate
description: Order-of-magnitude estimate when exact data is missing
when_to_use: "roughly how many/much", "estimate", "order of magnitude", "yaklaşık kaç"
---
1. Break the target into a product/sum of factors you can source or bound (write the equation first).
2. For each factor, search for a sourced value; if none exists, give a low and high bound with a one-line
   justification. Mark which factors are sourced and which are assumptions.
3. In `python`, compute the central estimate and the low/high range (propagate bounds; for many multiplied
   factors use geometric means or a quick Monte Carlo with log-uniform factors).
4. Report: central value with 1-2 significant figures, the range, the dominant uncertainty, and any sourced value
   of the target to compare against.

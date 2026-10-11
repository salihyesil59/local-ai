---
name: edge-cases
description: Checklist of edge cases to test during the review, before saying the work is done
when_to_use: the review after the last step; or whenever the tests pass but you are unsure the code is complete
---
Go through the list and add a test for every item that applies to this code (exact expected values):
- Empty and minimal input: empty string, empty list, one element, zero, None.
- Limits: the first and last allowed value, one beyond each limit, very large numbers (overflow, huge exponents).
- Wrong input: wrong type, malformed text, unknown name. Check what the request says should happen (an error
  value, an exception, a default) and test exactly that, never "any exception".
- Order and precedence: operators of different precedence together, nested brackets, right and left associativity,
  unary minus before a power.
- State: calling twice gives the same result, changing an input changes the dependent outputs, a failed call leaves
  no half-changed state.
- Propagation: an error in one input reaches every result that uses it, and goes away when the input is fixed.
- Every error the request lists: one test per error with an input that triggers it, and the result must have
  the form the request names (an error value or an exception of that type). Test the ways an input can be
  written wrongly (malformed number, unknown name, wrong type), because the code that reads the input is
  where a raw Python exception most often escapes. A rule such as "text in arithmetic is an error" covers
  every operator, not only the one in the example.
- Every form an argument may take (a number or a list or a tuple, a string or a path): test each form on
  EVERY function the request says accepts it, not only on the first one you wrote.
- Round trips: write then read gives back the same data, including text with commas, quotes and newlines.
- Case and whitespace where the request says names are case-insensitive.
Do not add tests for behaviour the request does not specify. If a test uncovers a bug, fix the code and call verify.

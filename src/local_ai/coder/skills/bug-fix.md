---
name: bug-fix
description: Fix a reported bug at its cause, with a test that fails before and passes after
when_to_use: the user reports wrong behaviour, an error or a failing test in existing code
keywords: bug, fix, hata, düzelt, error, crash, broken, fails, çalışmıyor, wrong result
---
1. Read the code the bug touches (search for the names in the report, outline, read_file). Do not guess from the
   report alone. Run the input of the report with run_python and look at the wrong output yourself.
2. Plan: step 1 reproduces the bug in a NEW failing test with the exact input and the expected output from the
   report. Step 2 fixes it. Criteria: the reproduction, plus every other behaviour the code must keep.
3. Write the failing test first and call verify. It asserts the exact expected output, not a property of it. It
   must fail for the reason in the report; if it passes, the test does not reproduce the bug: change the test,
   not the code.
4. Find the root cause: follow the failing input through the code and name the line where it first goes wrong.
   Do not trace long code in your head: print the values in between with run_python.
   Fix that line. Do not add a special case for the one input and do not catch the exception to hide it.
5. Call verify. If other tests break, your fix changed behaviour that was right: narrow the fix. Never change
   what an existing test expects, and add your tests next to the existing ones (edit_file), never in place of them.
6. In the review, add a test for a neighbouring input of the bug (empty, zero, negative, the other branch).

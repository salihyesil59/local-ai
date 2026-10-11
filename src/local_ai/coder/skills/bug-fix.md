---
name: bug-fix
description: Fix a reported bug at its cause, with a test that fails before and passes after
when_to_use: the user reports wrong behaviour, an error or a failing test in existing code
keywords: bug, fix, hata, düzelt, error, crash, broken, fails, çalışmıyor, wrong result
---
1. Read the code the bug touches (list_files, read_file). Do not guess from the report alone.
2. Plan: step 1 reproduces the bug in a NEW failing test with the exact input and the expected output from the
   report. Step 2 fixes it. Criteria: the reproduction, plus every other behaviour the code must keep.
3. Write the failing test first and call verify. It must fail for the reason in the report; if it passes, the test
   does not reproduce the bug: change the test, not the code.
4. Find the root cause: follow the failing input through the code and name the line where it first goes wrong.
   Fix that line. Do not add a special case for the one input and do not catch the exception to hide it.
5. Call verify. If other tests break, your fix changed behaviour that was right: narrow the fix.
6. In the review, add a test for a neighbouring input of the bug (empty, zero, negative, the other branch).

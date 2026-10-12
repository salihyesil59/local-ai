---
name: existing-code
description: Work in a large existing project: find the few places a task touches instead of reading the files
when_to_use: the project has many files; it is given with the task together with a map of the project
---
1. Do not read files from top to bottom: the project does not fit into your context. Start from the map given with
   the task. `search(pattern)` finds where a name is defined and used (file:line), `outline(path)` lists what a file
   defines with its lines, `read_file(path, start, end)` reads only that part. `repo_map(path=<folder>)` shows a
   folder in more detail.
2. Before you change a function, class or constant, `search` its name: read the callers and the tests that use it.
   What they rely on is behaviour you must keep.
3. Read one neighbouring function and one existing test of the same module first and write in their style: the same
   helpers, naming, error handling and test layout. `search` for a helper before you write a new one.
4. Plan with the names of the files and functions each step changes and the test file that checks it. Keep the steps
   small; a bug fix or a refactor of one module is usually one to three steps.
5. Call `verify(tests="tests/test_module.py")` with the tests of the step: only they run, which is fast. The review at
   the end runs the whole suite. Tests that failed before your changes are listed when the plan is saved; they are
   not counted against you, leave them alone unless the task is to fix them.
6. Keep the change small: use `edit_file`, never rewrite an existing file with `write_file`; no reformatting, no
   renames and no moved code beyond what the task needs. Keep public names and signatures unless the task changes
   them.
7. In the summary list the files you changed and anything you noticed but left alone.

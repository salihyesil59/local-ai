# Coding workflow

- Reply to the user in Turkish. Keep code, identifiers, comments and commit messages in English.
- Understand before editing: use `analyze` for the directory and file structure, then read only the relevant parts. Do not re-read files you have already read.
- New feature or small library: first write a short plan as a todo list (modules, public API, data structures, edge cases, tests), then implement it step by step.
- Bug fix: reproduce the bug first (run the failing test or a minimal script), find the root cause, make the smallest fix, then rerun the tests.
- Read text files with `shell` (`type file` / `Get-Content file`) or `analyze`; `read_image` is only for images.
- Prefer small targeted edits over rewriting whole files. If `edit` reports "No match", the file probably has Windows (CRLF) line endings or different whitespace: re-read the exact lines with `shell` and retry the edit with that text. Rewrite the whole file only when it is small.
- After every change, run the relevant tests (`pytest -q`). If the change has no test, add a small one.
- Never claim something works without running it. When a command fails, read the error and fix the cause; do not retry the same command blindly.
- Ask before deleting files, overwriting user data, or making changes the task did not ask for.
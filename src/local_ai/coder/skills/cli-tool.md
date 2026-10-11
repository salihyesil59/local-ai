---
name: cli-tool
description: Add a command-line interface to a library and test it
when_to_use: the request includes a command line (python -m package file, --option flags, printed output, exit codes)
keywords: command line, komut satırı, cli, argparse, python -m
---
1. Put the logic in functions that take arguments and return values; the CLI only parses arguments, calls them
   and prints. Use argparse from the standard library.
2. Write main(argv=None) -> int, with `if __name__ == "__main__": raise SystemExit(main())` and, for
   `python -m package`, a __main__.py that calls it.
3. Test main([...]) directly with capsys for the printed text and the return code; add one test that runs
   `python -m package` through subprocess with sys.executable to prove the entry point works.
4. Print exactly the format the request describes (separators, order, number formatting). Errors go to stderr with
   a non-zero return code; a bad path or option must not show a traceback.
5. Test every option the request lists, and the case where a required file does not exist.

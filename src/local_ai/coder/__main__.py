"""Command line: `local-coder` runs the MCP server, `local-coder check [project]` is the success check for
Goose recipes (exit code 0 only when every planned step is done and the verify command passes)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from local_ai.coder import state, workflow
from local_ai.coder.cli import add_arguments
from local_ai.servers.coder import server


def main() -> None:
    parser = argparse.ArgumentParser(prog="local-coder", description=__doc__)
    parser.add_argument(
        "--files", action="store_true", help="also offer read_file, write_file, edit_file and list_files (LM Studio)"
    )
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="exit 0 only when all steps are done and verification passes")
    check.add_argument("project", nargs="?", help="project folder (default: the current one, else the last planned)")
    run = sub.add_parser("run", help="run the coding agent (needs the 'agent' extra and LM Studio's server)")
    add_arguments(run)
    args = parser.parse_args()

    if args.command == "check":
        root = Path(args.project).expanduser().resolve() if args.project else state.active_root()
        ok, message = workflow.check(root)
        sys.stdout.reconfigure(errors="replace")  # test output may not fit the console's code page
        print(message)
        sys.exit(0 if ok else 1)
    if args.command == "run":
        from local_ai.coder import cli

        cli.main(args)
    server.main(args.files)


if __name__ == "__main__":
    main()

"""Docs MCP server: only the `docs` tool of the library server.

For coding agents (Goose) that need offline documentation lookup without the other tools inflating the prompt.
Archives are the devdocs_*.zim files in LOCAL_AI_ZIM_DIR (default ~/wiki-zim).

Run:  local-ai-docs
"""

from local_ai.servers.common import FastMCP
from local_ai.servers.library.zim import docs

mcp = FastMCP("docs")
mcp.tool()(docs)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

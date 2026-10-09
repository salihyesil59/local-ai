# Goose (coding)

All servers are stdio MCP servers, so they also work as Goose extensions:
`goose configure` → Add Extension → Command-line Extension, with the same command and env as in `mcp.json`.
[config/goose/config.example.yaml](../config/goose/config.example.yaml) has the extension entries for Goose's
`config.yaml`: `%APPDATA%\Block\goose\config\config.yaml` on Windows, `~/.config/goose/config.yaml` on Linux.

A setup that works well for coding with the local model:

- **Provider:** a custom OpenAI-compatible provider pointing at LM Studio (`http://localhost:1234`):
  - streaming on;
  - request timeout 1200 s;
  - the model name must match the LM Studio model key (check with `lms ls`).
- **Context:** `GOOSE_CONTEXT_LIMIT: 131072` in `config.yaml`; Goose uses it to decide when to compact. Only 10 of
  Qwen3.6's 40 layers use full attention, so 128k of KV cache is ~1.3 GB at Q8_0.
- **Extensions:**
  - On: `developer`, `analyze`, `todo`, `skills` and `docs` (`local-ai-docs`).
  - Added but disabled: `library` and `web`, turned on per session when needed (e.g. arXiv / Wikipedia / your
    library for physics code, or web search for a library bug). `library` also has `docs`, so either keep `docs`
    or `library` on, not both.
  - Goose stores the extension set per session, so start a new chat after changing them.
- **Rules:** use [config/goose/goosehints.md](../config/goose/goosehints.md) as the global `.goosehints`, next to
  `config.yaml`. It covers the local model's weak spots seen in tests:
  - plan first and reproduce bugs first;
  - read text files with the shell rather than `read_image`;
  - retry `edit` on CRLF files instead of rewriting them.
- **System prompt:** remove "show the full code before every write" style rules. They make a local model write
  every edit twice.

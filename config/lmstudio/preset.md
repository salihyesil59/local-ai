# Recommended LM Studio settings

For `salihyesil59/Qwen3.6-35B-A3B-RFT-Agent-GGUF` used as a research agent.
Save these as a preset (My Models → model → settings, or the preset menu in the chat sidebar)
so every research chat starts the same way.

| Setting | Value | Why |
|---|---|---|
| System prompt | contents of [`system-prompt.md`](system-prompt.md) | agent behavior, tool rules |
| Context length | 65536–131072 | tool outputs and notes add up quickly; only 10 of the 40 layers use full attention, so 128k of KV cache is ~1.3 GB at Q8_0 |
| Temperature | 0.6 | stable tool calls, still flexible wording |
| Top P | 0.95 | |
| Top K | 20 | Qwen3 recommendation |
| Min P | 0 | |
| Repeat penalty | 1.0 (off) | penalties corrupt JSON tool arguments |
| Thinking | on | better planning; `<think>` is stripped by the CLI agent |
| GPU offload | as many layers as fit | A3B MoE: only ~3B params active per token, so CPU offload of experts is still usable |
| Flash attention / KV cache quantization | on / Q8_0 | fits the longer context in memory |

## Embedding model (for `library` and memory)

Download a multilingual embedding model in LM Studio: the default is **Qwen3-Embedding-0.6B** (Q8_0, ~640 MB;
search "Qwen3-Embedding" in Model Search). It covers 100+ languages, so Turkish queries match English passages.
Load it alongside the chat model; LM Studio serves both at the same time. Its model id is `text-embedding-qwen3-embedding-0.6b` (shown in
Library → Text Embedding); for another model put its id in `LOCAL_AI_EMBED_MODEL` in `mcp.json` and run
`local-ai-index --reembed`. Without it, `library` and memory fall back to keyword (BM25) search.

## Tool calling

- Program → Install → **Edit mcp.json**, paste [`mcp.json.example`](mcp.json.example) (Windows) or [`mcp.linux.json.example`](mcp.linux.json.example) and fix the paths.
  It holds `library`, `compute` and `web`; the research agent has its own list ([`../agent/mcp.json.example`](../agent/mcp.json.example)).
- LM Studio asks for confirmation on every tool call by default. For the trusted local `library`
  and `notebook` servers you can choose "Always allow" per tool in the confirmation dialog so the
  agent can work through multi-step research without interruption.
- `compute`'s `python` executes arbitrary code on your machine. Keep confirmation on for
  them unless you trust the workflow; `compute` runs code inside `<workspace>/compute`.

## Speed

- **Parallel research** (`--parallel 2` in the CLI, or the Parallel option in the web UI) sends several
  sub-questions to the model at once. Enable parallel requests in LM Studio's server settings
  (max concurrent predictions); each parallel slot needs its own KV cache, so lower the context length or
  use KV-cache quantization if memory gets tight. Identical searches from different sub-questions are
  shared automatically.
- **Speculative decoding**: in the model's settings pick a small draft model from the same family (it must share
  the tokenizer, e.g. a small Qwen3 model for a Qwen3-based model). Compare tokens/s with and without it on a
  typical research prompt; the gain depends on how predictable the output is and is often smaller for MoE
  models, so keep it only if it is faster on your machine.

## CLI agent

The CLI agent (`local-ai`) talks to the same model through LM Studio's local server:
Developer tab → **Start Server** (default `http://localhost:1234`). It reads its own `mcp.json` (`LOCAL_AI_MCP_CONFIG`), and
sampling settings are passed per request (temperature/top_p), while context length, GPU offload and
other load-time settings come from how the model is loaded in LM Studio.

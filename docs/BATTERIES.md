# Batteries

Alfred runs fully with zero batteries. The built-ins below are always on and need no setup. The opt-
in batteries are enhancements you can turn on when you want them, each with a plain description of
what it is and what you get. Turn them on interactively in the desktop onboarding (the battery
picker) or from the CLI with `alfred batteries`; enabling one writes its env flags to
`$ALFRED_HOME/.env`. For the memory back ends specifically, see
[`MEMORY_PROVIDERS.md`](MEMORY_PROVIDERS.md).

```sh
alfred batteries               # list every battery and its current status
alfred batteries enable <id>   # turn an opt-in battery on
alfred batteries disable <id>  # turn it back off
```

## Built-in (always on)

| Battery | id | What it is | What you get | Install |
|---|---|---|---|---|
| **Built-in memory** (Memory) | `sqlite-memory` | An embedded SQLite lesson store with keyword (BM25) recall, kept in a single file. | Alfred remembers what it learned on past runs and pulls the relevant lessons back in, with zero setup and no server to run. | Built in, no setup |
| **Tool-output compactor** (Context / compression) | `tool-compactor` | A built-in compactor that trims verbose command, test, and log output before it is stored. | Keeps noisy tool output from filling the context window, so more of each run's budget goes to real work. Nothing to install. | Built in, no setup |
| **Skeleton and delta reads** (Code understanding) | `skeleton-reads` | A local code index that lets the agent read a file's outline, and only what changed since last time. | The agent gets its bearings in a file from a compact outline instead of re-reading the whole thing, which saves tokens and time. Built in, no external index. | Built in, no setup |
| **Blast radius** (Code understanding) | `blast-radius` | A local impact check that flags what else a change might touch, from Alfred's own code map. | Before an edit, the agent can see roughly what depends on the code it is about to change, so it is less likely to break something out of sight. Advisory, and built in. | Built in, no setup |

## Opt-in

| Battery | id | What it is | What you get | Install |
|---|---|---|---|---|
| **Dense embeddings** (Memory) | `dense-embeddings` | A vector (semantic) recall arm on the built-in SQLite store, fused with the keyword arm. | Finds relevant past lessons even when you word things differently, because it matches on meaning as well as keywords. Stays a single file; needs a local Ollama for the embeddings. | `pip install "alfred-os[vector]"` |
| **Headroom compression** (Context / compression) | `headroom-compression` | An optional external compressor (headroom-ai) wired in behind the same tool-output seam. | Squeezes more out of verbose logs, JSON, and test output than the built-in compactor, lowering the token cost of each run. Optional; if it is missing Alfred just uses the built-in. | Install `headroom-ai` into the same `python3` that runs Alfred's hooks, for example `python3 -m pip install headroom-ai` in the scheduler environment. `pipx install headroom-ai` is CLI-only unless you also set `ALFRED_HEADROOM_COMPRESS_CMD`. |
| **Codebase memory (MCP)** (Code understanding) | `code-memory-mcp` | A standalone MIT binary (codebase-memory-mcp) that indexes your repos into a code graph the agent queries over MCP. | Lets the agent ask where a symbol is, who calls it, and what a change would affect, instead of grepping and re-reading. Alfred attaches it to Claude-engine firings by default once the pinned binary is present. | Fetched automatically on first use by default. Set `ALFRED_CODE_MEMORY_MCP=0` to disable the MCP attachment or `ALFRED_CODE_MEMORY_AUTOFETCH=0` to require a manual binary install. |
| **Redis Agent Memory Server** (Memory) | `redis-ams` | A daemon-backed semantic memory store (Redis Agent Memory Server), used instead of the embedded SQLite store. | Shares one semantic memory across many machines, for when a single file on one host is not enough. It needs Redis, the memory server, and Ollama running; the SQLite default needs none of that, so most solo setups do not need this. | Needs a Redis you run |
| **Postgres + pgvector** (Memory) | `pgvector` | The scale-tier memory backend: Postgres with pgvector, behind the same memory contract. | Handles the case where the single-file SQLite store becomes the bottleneck (many machines writing at once, or very large lesson counts). Needs a Postgres you run. Stay on SQLite until you actually hit that wall. | Install `alfred-os[pgvector]` into Alfred's runtime Python and run Postgres with the pgvector extension, for example `$ALFRED_HOME/venv/bin/python -m pip install "alfred-os[pgvector]"`. |

## Notes

- The default memory store is the built-in embedded SQLite keyword store plus the local FleetBrain relational ledger, so recall works with no daemon. Dense vector recall is off until you enable `dense-embeddings` and install its optional dependency.
- **Codebase memory (`code-memory-mcp`)** is an opt-in *install* (Alfred fetches the pinned binary on first use), but once that binary is present it is attached to Claude-engine firings **by default**. Turn it off with `ALFRED_CODE_MEMORY_MCP=0`.
- `redis-ams` and `pgvector` are alternative memory back ends for larger or heavily concurrent installs; they are mutually exclusive as the primary store and layer in front of the built-in SQLite chain. `pgvector` needs both the `alfred-os[pgvector]` Python extra and a Postgres you run.
- Everything here is derived from the battery manifest in `lib/batteries.py`, the single source of truth shared by the CLI and the desktop picker.

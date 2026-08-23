# Batteries

Alfred includes local memory, context compaction, structural reads, blast-radius checks, and
codebase memory. After you configure a valid repository scope, Alfred can fetch and verify the
pinned codebase-memory binary. It performs no binary or cache work while scope is empty. The
other included tools need no setup.

Setup separates the remaining choices into optional local tools and external services. Optional
local tools install on the Alfred host. External services need a separate process that you
operate. Manage configurable tools in Desktop Settings, during onboarding, or with
`alfred batteries`. An explicit
disable is written to `$ALFRED_HOME/.env` and always wins. For memory back ends, see
[`MEMORY_PROVIDERS.md`](MEMORY_PROVIDERS.md).

```sh
alfred batteries               # list every battery and its current status
alfred batteries enable <id>   # turn a configurable battery on
alfred batteries disable <id>  # turn it back off
alfred batteries remove <id>   # remove a disabled local dependency
```

`remove` never changes `$ALFRED_HOME/.env`. Disable the battery first. Alfred removes only the
dependency it installed. It does not remove an operator-supplied executable, database, model, or
service.

## Included

| Battery | id | What it is | What you get | Install |
|---|---|---|---|---|
| **Built-in memory** (Memory) | `sqlite-memory` | An embedded SQLite lesson store with keyword (BM25) recall, kept in a single file. | Alfred remembers what it learned on past runs and pulls the relevant lessons back in, with zero setup and no server to run. | Built in, no setup |
| **Tool-output compactor** (Context / compression) | `tool-compactor` | A built-in compactor that trims verbose command, test, and log output before it is stored. | Keeps noisy tool output from filling the context window, so more of each run's budget goes to real work. Nothing to install. | Built in, no setup |
| **Skeleton and delta reads** (Code understanding) | `skeleton-reads` | A local code index that lets the agent read a file's outline, and only what changed since last time. | The agent gets its bearings in a file from a compact outline instead of re-reading the whole thing, which saves tokens and time. Built in, no external index. | Built in, no setup |
| **Blast radius** (Code understanding) | `blast-radius` | A local impact check that flags what else a change might touch, from Alfred's own code map. | Before an edit, the agent can see roughly what depends on the code it is about to change, so it is less likely to break something out of sight. Advisory, and built in. | Built in, no setup |
| **Codebase memory (MCP)** (Code understanding) | `code-memory-mcp` | A standalone MIT binary (codebase-memory-mcp) that indexes your repos into a code graph the agent queries over MCP. | Lets the agent ask where a symbol is, who calls it, and what a change would affect, instead of grepping and re-reading. | Set an explicit repository scope. Alfred then fetches and verifies the pinned binary, or uses the executable path you configured. Set `ALFRED_CODE_MEMORY_MCP=0` to disable it. |

## Optional local tools

| Battery | id | What it is | What you get | Install |
|---|---|---|---|---|
| **Headroom compression** (Context / compression) | `headroom-compression` | An optional Headroom compressor that uses the same failure and size gates as the built-in compactor. | Lets you benchmark Headroom against the built-in compactor on recorded output before you enable it. Alfred uses the built-in when Headroom returns no acceptable result. | `alfred batteries enable headroom-compression` installs the pinned `headroom-ai==0.29.0` package into the Python interpreter that runs Alfred's hooks. A CLI-only install is not enough unless `ALFRED_HEADROOM_COMPRESS_CMD` names a working stdin-to-stdout command. |
| **Graphify code graph** (Code understanding) | `graphify` | A pure-Python code-graph engine (graphifyy, tree-sitter over ~40 languages) that maps imports, calls, and inheritance into a queryable graph, served to the agent over MCP. | The agent navigates a large codebase by real relationships instead of re-reading files, and extraction is local with no LLM, database, or embeddings. An alternative to Codebase memory (MCP); enable one code-graph engine, not both. | Alfred installs the pinned tool under its home directory (`~/.alfred` by default) when you enable it. Set `ALFRED_GRAPHIFY_MCP=1` to enable, or `ALFRED_GRAPHIFY_FALLBACK` to control its fallback. |

## External services

| Battery | id | What it is | What you get | Install |
|---|---|---|---|---|
| **Dense embeddings** (Memory) | `dense-embeddings` | A vector recall arm on the built-in SQLite store. It needs `sqlite-vec` and an Ollama process with the configured embedding model. | Finds lessons that use different wording. The keyword arm remains available when the embedding service is down. | Run `alfred batteries install dense-embeddings --yes`, then install and run Ollama. Alfred does not install Ollama or pull a model. |
| **Redis Agent Memory Server** (Memory) | `redis-ams` | A daemon-backed semantic memory store (Redis Agent Memory Server), used instead of the embedded SQLite store. | Shares one semantic memory across many machines, for when a single file on one host is not enough. It needs Redis, the memory server, and Ollama running; the SQLite default needs none of that, so most solo setups do not need this. | Needs a Redis you run |
| **Postgres + pgvector** (Memory) | `pgvector` | The scale-tier memory backend: Postgres with pgvector, behind the same memory contract. | Handles the case where the single-file SQLite store becomes the bottleneck (many machines writing at once, or very large lesson counts). Needs a Postgres you run. Stay on SQLite until you actually hit that wall. | Install `alfred-os[pgvector]` into Alfred's runtime Python and run Postgres with the pgvector extension, for example `$ALFRED_HOME/venv/bin/python -m pip install "alfred-os[pgvector]"`. |

## Source and removal record

The JSON manifest returned by `alfred batteries --json` carries the same fields for every row:
`version`, `license`, `source_url`, `integrity`, `install_command`, `check_command`,
`disable_command`, and `remove_command`.

| Battery | Version | Licence | Integrity | Removal |
|---|---|---|---|---|
| Built-in memory, compactor, skeleton reads, blast radius | Bundled with Alfred | MIT | Installed with the signed Alfred package | Removed with Alfred |
| Codebase memory | `v0.8.1` | MIT | Release `checksums.txt`, verified with SHA-256 | `alfred batteries remove code-memory-mcp --yes` removes only Alfred's cached binary |
| Headroom | `headroom-ai==0.29.0` | Apache-2.0 | Python package index artifact hashes | `alfred batteries remove headroom-compression --yes` |
| Graphify | `graphifyy[mcp]==0.9.8`; `mcp==1.28.1` | MIT | Python package index artifact hashes | `alfred batteries remove graphify --yes` removes only Alfred's uv tool under `$ALFRED_HOME`; operator-supplied executables are unchanged |
| Dense embeddings | `sqlite-vec>=0.1`; Ollama operator-managed | sqlite-vec MIT OR Apache-2.0; Ollama CLI MIT; model licence varies | Python package index artifact hashes; operator-managed Ollama | `alfred batteries remove dense-embeddings --yes` removes `sqlite-vec`; remove Ollama and models yourself |
| Redis Agent Memory Server | Operator-managed compatible service | Apache-2.0 | Operator-managed image or source checkout | Stop and remove the service after disabling the battery |
| Postgres + pgvector | `psycopg>=3.1`; `pgvector>=0.2`; Postgres operator-managed | psycopg LGPL-3.0; pgvector-python MIT; pgvector PostgreSQL | Python package index artifact hashes; operator-managed database | Remove the database and local packages after disabling the battery |

## Notes

- The default memory store is the built-in embedded SQLite keyword store plus the local FleetBrain relational ledger, so recall works with no daemon. Dense vector recall is off until you enable `dense-embeddings` and install its optional dependency.
- **Codebase memory (`code-memory-mcp`)** is included by default. Set `ALFRED_CODE_MEMORY_REPOS` or `ALFRED_CODE_MAP_REPOS` before use. A resolved scope enables pinned binary fetch and its isolated cache. Alfred never selects repositories from the workspace on its own. Turn it off with `ALFRED_CODE_MEMORY_MCP=0`.
- `redis-ams` and `pgvector` are alternative memory back ends for larger or heavily concurrent installs; they are mutually exclusive as the primary store and layer in front of the built-in SQLite chain. `pgvector` needs both the `alfred-os[pgvector]` Python extra and a Postgres you run.
- Everything here is derived from the battery manifest in `lib/batteries.py`, the single source of truth shared by the CLI and the desktop picker.

# Code memory (code-structure layer)

Alfred's memory has three layers, each answering a different question:

| Layer | Question it answers | Backend |
|---|---|---|
| Semantic lessons | "What did a past firing learn about this repo?" | Embedded SQLite hybrid memory by default |
| Operational graph | "What relations has the fleet recorded?" | FleetBrain / AGE graph |
| **Code structure** | "Where is this symbol, who calls it, and what can this change affect?" | **codebase-memory-mcp** |

This doc covers the third layer. The first two are in
[MEMORY_PROVIDERS.md](MEMORY_PROVIDERS.md) and [FLEET_BRAIN.md](FLEET_BRAIN.md).

## What it is

[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
(DeusData, MIT) is a standalone binary that indexes your in-scope repositories
into a code graph and answers read-only structure queries over MCP. Alfred
attaches it as an MCP server on Claude-engine firings only (Codex-routed firings
get no MCP), so the fleet agents get code-structure tools the model can call on
demand:

- **search** the graph and source for symbols, definitions, and references
- **trace** callers and callees for a function
- **detect** changed symbols and their graph impact
- **query** the graph schema, snippets, and architecture summary

The binary is **never vendored** into this repository. Alfred invokes it as an
external process, so the alfred-os tree stays clean and passes `scrub-check`.
The launcher fetches a pinned release on first use (opt-out), or you can point
it at a binary you installed yourself.

## How it is wired

- **MCP attachment.** `lib/agent_runner/process.py` attaches the
  `code_memory` server to each `claude` firing in the same `--mcp-config` as
  the read-only memory server, and adds its tools to the agent allowlist. It is
  a capability, on by default, and degrades to a clean no-op when the binary is
  not installed.
- **Launcher.** `bin/code-memory-mcp` resolves the binary, fetches the pinned
  release if needed, and runs the stdio MCP server (`serve`) or rebuilds the
  index (`index` / `refresh`). Run `bin/code-memory-mcp doctor` to see what is
  resolved.
- **Indexing.** The launcher indexes the repos in your scope list into
  `$ALFRED_HOME/state/code-memory`. Set `ALFRED_CODE_MEMORY_REPOS`, or set
  `ALFRED_CODE_MAP_REPOS` as the fallback scope. Alfred does not infer a runtime
  scope from nearby checkouts. The interactive repository picker can scan for
  choices, but it does not enable those repositories. Alfred indexes only scope
  entries that resolve to git repositories. The
  installed `code-map-refresh` agent keeps Alfred's lightweight local JSON code map
  current. The `code-memory-mcp` launcher refreshes the MCP graph separately so
  graph search, call traces, change detection, and architecture queries track git changes without a
  full rebuild.
- **Stable local export.** `alfred code-map export` converts
  `$ALFRED_HOME/state/code-map.json` into the stable `alfred-codegraph@1`
  contract. This is the deterministic local fallback for agents, MCP clients,
  and onboarding checks when the external code-memory binary is not installed.
- **Read-only MCP bridge.** `alfred mcp serve` exposes
  `alfred_code_graph_summary`, `alfred_code_impact`, and
  `alfred_code_blast_radius` alongside the existing memory tools. Agents can
  ask for repo summaries, single-file import impact, matching symbols, API
  calls, contract drift, and multi-file blast radius without reading raw
  transcripts or shelling out.

## Install and index

```sh
# Resolve + fetch the pinned binary, then build the initial index.
bin/code-memory-mcp doctor      # shows resolved binary, version pin, index dir
bin/code-memory-mcp index       # full build for the in-scope repos
bin/code-memory-mcp refresh     # incremental rebuild of the MCP graph

# The full fleet also installs code-map-refresh for the local JSON code map.
alfred agents                   # confirm code-map-refresh appears

# Stable local contract for native onboarding and agent fallback context.
alfred code-map build . --output /tmp/code-map.json --json
alfred code-map export --summary-only
alfred code-map summary
alfred code-map impact frontend src/lib/api.ts --json
alfred code-map impact frontend src/lib/api.ts --brief
alfred code-map blast-radius frontend src/lib/api.ts src/App.tsx --json
```

If the scope is not configured, or none of its entries resolve to git
checkouts, the MCP server is a no-op for that firing. Explicit `index` and
`refresh` actions fail with a scope error. Alfred checks the resolved scope
before it fetches or runs the binary. If the binary cannot be resolved, the MCP
server is also a no-op and the rest of memory is unaffected.

## Configuration

All knobs are environment variables. Set them in `$ALFRED_HOME/.env`.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_CODE_MEMORY_MCP` | `1` (on) | Attach the code-memory MCP to Claude firings. Set `0` to disable. |
| `ALFRED_CODE_MEMORY_REPOS` | (falls back to `ALFRED_CODE_MAP_REPOS`) | Required comma-separated repo directory names or slugs to index. An empty scope disables serving and blocks indexing. |
| `ALFRED_REPO_LOCAL_MAP` | (unset) | Optional shell-tokenized `repo-slug=local-path` map for repos whose GitHub slug differs from the checkout directory, for example `ALFRED_REPO_LOCAL_MAP='acme-api=api acme-site=../marketing/site'`. Relative paths resolve under the configured workspace subdir. |
| `ALFRED_WORKSPACE_SUBDIR` | (falls back to `WORKSPACE_SUBDIR`, then `product`) | Optional subdirectory under `WORKSPACE_ROOT` where configured relative repo paths resolve. Set an empty value to resolve them from `WORKSPACE_ROOT`. |
| `ALFRED_CODE_MEMORY_BIN` | (unset) | Trusted executable path to `codebase-memory-mcp`. This bypasses Alfred's pinned download verification. A missing or non-executable path fails closed and does not use the cache or auto-fetch. |
| `ALFRED_CODE_MEMORY_VERSION` | pinned (`v0.8.1`) | Upstream release tag to fetch. |
| `ALFRED_CODE_MEMORY_REPO` | `DeusData/codebase-memory-mcp` | Upstream GitHub repo for release assets. |
| `ALFRED_CODE_MEMORY_AUTOFETCH` | `1` (on) | Fetch the pinned binary on first use. Set `0` for a strict no-network install. |
| `ALFRED_CODE_MEMORY_CONNECT_TIMEOUT_S` | `10` | Connect timeout for first-use release downloads. |
| `ALFRED_CODE_MEMORY_FETCH_TIMEOUT_S` | `120` | Overall timeout for first-use release downloads. |
| `ALFRED_CODE_MEMORY_INDEX_DIR` | `$ALFRED_HOME/state/code-memory` | Default storage root for code-memory state when `ALFRED_CODE_MEMORY_HOME` is unset. |
| `ALFRED_CODE_MEMORY_HOME` | `ALFRED_CODE_MEMORY_INDEX_DIR` | HOME used for the upstream binary and the default root for graph caches. |
| `CBM_CACHE_DIR` | `$ALFRED_CODE_MEMORY_HOME/.cache/codebase-memory-mcp` | Optional graph-cache root. Alfred creates one deterministic subdirectory per exact resolved repository scope. |

Relative values for the index, code-memory home, and cache roots resolve under
`ALFRED_HOME`. The launcher and Setup therefore inspect the same physical cache
even when they start from different working directories.

## `alfred-codegraph@1`

The export contract is intentionally small:

- `schema`: always `alfred-codegraph@1`
- `generated_at`: timestamp from the last `code-map-refresh`
- `repos[]`: repo name, HEAD SHA, graph summary, contract surfaces, and
  optionally files plus import edges
- `contract_drift[]`: client API calls with no matching server endpoint or
  route in the local map

The impact query resolves simple relative imports (`./Widget`, `./api`) back to
mapped files and returns incoming imports, outgoing imports, symbols, API
surfaces in the file, matching drift, nearby files, and a `match_status`
(`exact`, `suffix`, `ambiguous`, or `not_found`). It is advisory context, not a
compiler or merge gate.

For prompt-ready planning context, `alfred code-map impact ... --brief` renders
the same facts as a concise single-file blast-radius note. For branch-sized
changes, `alfred code-map blast-radius <repo> <path...>` aggregates multiple
changed paths, dedupes direct dependents, calls out contract surfaces and drift,
and returns a simple `low` / `medium` / `high` local risk label with next checks.
It is still advisory: refresh the map or inspect manually when paths are
unmapped, ambiguous, generated, or hidden behind dynamic imports.

Binary resolution order (first hit wins):

1. `ALFRED_CODE_MEMORY_BIN` if it points at an executable.
2. `$ALFRED_HOME/bin/codebase-memory-mcp`, the pinned cache that Alfred can fetch.

Alfred does not use an ambient `codebase-memory-mcp` executable from `PATH`.
If `ALFRED_CODE_MEMORY_BIN` is set but invalid, Alfred stops resolution. It does
not fall back to another binary.

## Scope

The code-memory layer is **read-only** structure intelligence. It never edits
repositories, never writes lessons, and never replaces the semantic-lesson or
operational-graph layers. It complements them: lessons say what Alfred learned,
the graph says what the fleet recorded, and code memory says how the code is
actually shaped right now.

## Privacy

The binary runs locally and Alfred asks it to index only the repositories you
list. Fetching the binary contacts GitHub releases. Disable that with
`ALFRED_CODE_MEMORY_AUTOFETCH=0` and set `ALFRED_CODE_MEMORY_BIN` to a binary
that you installed.

Alfred derives each active graph-cache directory from a SHA-256 fingerprint of
the sorted canonical paths in the resolved repository scope. Repository order
does not affect the fingerprint. A different or narrower scope uses a different
cache, so the MCP server cannot query graphs retained under an older scope.
Old scope caches remain on disk until you remove them. Alfred does not delete or
migrate them automatically.


## Graphify: an alternative code-graph engine (opt-in)

`graphify` is an optional, pure-Python code-graph engine you can run *instead of*
`codebase-memory-mcp`. It parses your repos with tree-sitter (~40 languages) into
a `graphify-out/graph.json` and serves that graph read-only over MCP, so the agent
can ask for a symbol's neighbours, the shortest path between two symbols, or a
subsystem summary rather than re-reading files. Extraction is local and needs no
LLM, database, or embeddings.

It is **mutually exclusive** with `codebase-memory-mcp`: both attach as the single
code-graph MCP server, so Alfred runs at most one. When `ALFRED_GRAPHIFY_MCP` is
on and that repo's graph is ready, Graphify takes the slot. An explicit fallback
may occupy the same slot only while the Graphify graph is unavailable.

Setup:

```sh
alfred batteries enable graphify --yes  # installs pinned graphifyy[mcp] with uv
graphify /path/to/repo           # first build: graphify-out/graph.json (no LLM)
graphify /path/to/repo --update  # later incremental refresh
```

Then enable it (or tick it in `alfred batteries` / the desktop battery picker):

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_GRAPHIFY_MCP` | `0` (off) | Attach graphify's read-only graph MCP to firings, taking the code-graph slot. |
| `ALFRED_GRAPHIFY_FALLBACK` | unset (`code-memory` when enabled through the battery picker) | Explicit engine to use while a repo has no Graphify graph. Set `none` to leave the slot empty instead. |
| `ALFRED_GRAPHIFY_BIN` | auto | Override the `graphify-mcp` executable path. Alfred otherwise uses the installed entrypoint. Package installation happens during battery setup, never inside an agent firing. |
| `ALFRED_GRAPHIFY_GRAPH` | `graphify-out/graph.json` | Graph file passed to the MCP server, relative to each firing's repo worktree unless absolute. |

A firing serves the graph in its own working directory (`graphify-out/graph.json`),
so build or update the graph per repo. If that repo has no graph yet, Alfred does
not attach Graphify. Manual configuration falls back only when
`ALFRED_CODE_MEMORY_MCP` remains enabled. The battery picker records the explicit
`code-memory` fallback while still disabling its normal gate, so an unindexed
repo keeps one structural engine and the fallback is visible in configuration.
Autofetch stays enabled for that fallback, but code-memory is not attached while
Graphify has a usable graph.

The read-only tools it exposes: `query_graph`, `get_node`, `get_neighbors`,
`get_community`, `god_nodes`, `graph_stats`, `shortest_path`, `list_prs`,
`get_pr_impact`, `triage_prs`.

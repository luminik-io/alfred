# Code memory (code-structure layer)

Alfred's memory has three layers, each answering a different question:

| Layer | Question it answers | Backend |
|---|---|---|
| Semantic lessons | "What did a past firing learn about this repo?" | Redis Agent Memory (vectors) |
| Operational graph | "What relations has the fleet recorded?" | FleetBrain / AGE graph |
| **Code structure** | "Where is this symbol, who calls it, what breaks if I change it, who owns it?" | **codebase-memory-mcp** |

This doc covers the third layer. The first two are in
[MEMORY_PROVIDERS.md](MEMORY_PROVIDERS.md) and [FLEET_BRAIN.md](FLEET_BRAIN.md).

## What it is

[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
(DeusData, MIT) is a standalone binary that indexes your in-scope repositories
into a code graph and answers read-only structure queries over MCP. Alfred
attaches it as an MCP server on Claude-engine firings only (Codex-routed firings
get no MCP), so the fleet agents get code-structure tools the model can call on
demand:

- **search** the code graph for symbols, definitions, and references
- **call graph** for a function (callers and callees)
- **impact / blast radius** for a proposed change
- **who-owns** a file or symbol

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
  `$ALFRED_HOME/state/code-memory`. If no scope list is configured, Alfred
  auto-discovers git repos under `WORKSPACE_ROOT/product` by default, skipping
  archive, worktree, build, and dependency directories. Once a scope list is
  configured, Alfred indexes only entries that resolve to real git repos; stale
  entries are skipped instead of falling back to broad auto-discovery. The
  installed `code-map-refresh` agent keeps Alfred's lightweight local JSON code map
  current. The `code-memory-mcp` launcher refreshes the MCP graph separately so
  search, call-graph, impact, and who-owns queries track git changes without a
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

If the binary cannot be resolved (no network, autofetch disabled, unsupported
platform), the MCP server is a no-op for that firing and the rest of memory is
unaffected. Nothing fails closed.

## Configuration

All knobs are environment variables; set them in `$ALFRED_HOME/.env`.
Defaults work out of the box.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_CODE_MEMORY_MCP` | `1` (on) | Attach the code-memory MCP to Claude firings. Set `0` to disable. |
| `ALFRED_CODE_MEMORY_REPOS` | (falls back to `ALFRED_CODE_MAP_REPOS`, then auto-discovery) | Comma-separated repo dir names under your workspace to index. |
| `ALFRED_REPO_LOCAL_MAP` | (unset) | Optional shell-tokenized `repo-slug=local-path` map for repos whose GitHub slug differs from the checkout directory, for example `ALFRED_REPO_LOCAL_MAP='acme-api=api acme-site=../marketing/site'`. Relative paths resolve under the configured workspace subdir. |
| `ALFRED_CODE_MEMORY_DISCOVERY_LIMIT` | `25` | Max git repos auto-discovered when no explicit code-memory/code-map scope is configured. |
| `ALFRED_WORKSPACE_SUBDIR` | (falls back to `WORKSPACE_SUBDIR`, then `product`) | Optional subdirectory under `WORKSPACE_ROOT` to scan for code-memory repos. Set it to an empty value to scan `WORKSPACE_ROOT` directly. |
| `ALFRED_CODE_MEMORY_BIN` | (unset) | Explicit path to the `codebase-memory-mcp` binary. Skips PATH + autofetch. |
| `ALFRED_CODE_MEMORY_VERSION` | pinned (`v0.8.1`) | Upstream release tag to fetch. |
| `ALFRED_CODE_MEMORY_REPO` | `DeusData/codebase-memory-mcp` | Upstream GitHub repo for release assets. |
| `ALFRED_CODE_MEMORY_AUTOFETCH` | `1` (on) | Fetch the pinned binary on first use. Set `0` for a strict no-network install. |
| `ALFRED_CODE_MEMORY_CONNECT_TIMEOUT_S` | `10` | Connect timeout for first-use release downloads. |
| `ALFRED_CODE_MEMORY_FETCH_TIMEOUT_S` | `120` | Overall timeout for first-use release downloads. |
| `ALFRED_CODE_MEMORY_INDEX_DIR` | `$ALFRED_HOME/state/code-memory` | Default storage root for code-memory state when `ALFRED_CODE_MEMORY_HOME` is unset. |
| `ALFRED_CODE_MEMORY_HOME` | `ALFRED_CODE_MEMORY_INDEX_DIR` | HOME used for the upstream binary, which stores graph DBs under `.cache/codebase-memory-mcp`. |

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

1. `ALFRED_CODE_MEMORY_BIN` if it points at an executable
2. `codebase-memory-mcp` on `PATH` (system or package install)
3. `$ALFRED_HOME/bin/codebase-memory-mcp` (the pinned cache, auto-fetched here)

## Scope

The code-memory layer is **read-only** structure intelligence. It never edits
repositories, never writes lessons, and never replaces the semantic-lesson or
operational-graph layers. It complements them: lessons say what Alfred learned,
the graph says what the fleet recorded, and code memory says how the code is
actually shaped right now.

## Privacy

The binary runs locally and indexes only the repos you list. No code, symbols,
or graph data leave the host. Fetching the binary contacts GitHub releases
only; disable that with `ALFRED_CODE_MEMORY_AUTOFETCH=0` and install the binary
yourself.

## Phase 2: typed, linked, and time-aware lessons

Phase 1 gave lessons semantic recall (a body, tags, severity) in the embedded
SQLite hybrid store. Phase 2 adds **structure** on top of that same store (and
the FleetBrain ledger), so a lesson is no longer a flat sentence. Every part is
**additive, off by default, and backward-compatible**: with nothing enabled, an
older untyped lesson reads and recalls exactly as before, and the schema
migrates in place through guarded `ALTER TABLE ... ADD COLUMN` calls (the same
idempotent pattern the rest of the brain uses). Phase 2 **feeds** the existing
capture -> judge -> promote pipeline; it never replaces it.

### 1. Typed lessons (`kind`)

Each lesson carries a `kind` from a small taxonomy
(`lib/fleet_brain/taxonomy.py`):

| kind | what it captures |
|---|---|
| `convention` | a durable repo convention (where things live, how they are named) |
| `fix` | a concrete fix that worked for a class of bug |
| `failure` | a mistake or gotcha to avoid |
| `decision` | a decision the fleet made and should not relitigate |
| `review-pattern` | a recurring review finding |
| `note` | the neutral default; also where an **untyped legacy lesson lands** |

`note` is deliberately not one of the five differentiating kinds: an old row
reads back as `note` rather than being mislabelled as a convention it was never
asserted to be. Unknown or aliased kinds fold to a canonical value and never
raise.

**Type-aware recall** (`ALFRED_MEMORY_TYPED_RECALL`, off by default) prefers the
kinds that matter when editing code: conventions first, then review-patterns and
fixes, then the failures to avoid, ahead of passive notes. It is a stable
reordering applied after the existing rank pass, so relevance still orders
lessons within a kind bucket and the default output is byte-for-byte unchanged.

### 2. Code-grounding anchors

A `lesson_anchors` table links a lesson to the code entity it is about (a
`file`, `symbol`, or graph `node`) or to another `lesson`
(`supersedes` / `related` / `contradicts`). The write is idempotent on
`(lesson_id, anchor_type, anchor_ref, relation)`.

The pay-off is anchored recall: pass the files a firing is about to edit as
`anchor_refs=[...]` and the store surfaces "editing `auth.py` -> the convention +
the fix that worked + the mistake to avoid" **first**, before the general
lexical/dense hits. `lessons_for_anchor(anchor_ref=...)` is the direct read:
"what does the fleet know about this file." Anchors reference the same node-id
shape as the fleet graph (`file:<repo>/<path>`), so they compose with the
`graph_edges` layer without a graph database.

### 3. Validity + provenance (invalidate, never delete)

Two columns give a lesson bi-temporal validity: `valid_until` (when it stops
being true) and `superseded_by` (the lesson that replaced it). Recall always
filters these out, so a superseded or expired lesson silently stops surfacing
while its **row survives for audit**. The filter is inert until something is
actually superseded, so default recall is unchanged.

`supersede_lesson(old, new)` is the supersede primitive: it stamps the old row,
records a `supersedes` lesson-to-lesson anchor, and leaves the audit trail
intact. A new lesson that contradicts an existing one supersedes it rather than
piling up a near-duplicate. Every promoted lesson also records `provenance` (the
firing or PR that created it), which defaults to the firing id when not given.

### 4. Deterministic repo-profile injector

`lib/agent_runner/repo_profile.py` builds a small, **deterministic** profile of a
repo from what Alfred can already see on disk: the manifest(s) and package
manager, the exact test/lint/build commands to verify with, the
agent-instruction files, and a one-line structure summary. It is injected as a
convention-memory block so a headless firing does not re-discover the project's
shape every run.

The idea is ported from Hermes' `coding_context.build_coding_workspace_block`,
adapted to Alfred's headless model: no interactive session lifecycle, and
**no live `git status`** (which drifts), so the same tree always yields a
byte-identical block. Injection is gated by `ALFRED_REPO_PROFILE` (off by
default) and bounded to a character budget (`ALFRED_REPO_PROFILE_MAX_CHARS`,
default 1200) so "profile on" can never balloon the run prompt. It is
independent of the recall provider, so it can orient a firing even when memory
recall is empty.

### Phase 2 configuration

All off by default; set in `$ALFRED_HOME/.env`.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_MEMORY_TYPED_RECALL` | `0` (off) | Prefer conventions + fixes by lesson `kind` in recall order. |
| `ALFRED_REPO_PROFILE` | `0` (off) | Inject the deterministic repo-profile block into each firing. |
| `ALFRED_REPO_PROFILE_MAX_CHARS` | `1200` | Character budget for the injected repo-profile block. |

Typed lessons, anchors, and validity are always **stored** (they are schema, not
behaviour); only their recall-shaping effects are gated. A/B against the Phase 1
numbers with `alfred benchmark` by toggling the flags above.

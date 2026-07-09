# Skeleton and delta reads

Agents waste tokens re-reading whole files. A firing reads a file to orient,
then reads it again a few turns later for a two-line change, paying the full
file each time. This battery gives two complementary savings that reuse the code
map Alfred already builds. It adds no vector database, embeddings, or new store.

| Saving | What it does | Backend |
|---|---|---|
| **Skeleton** | Return a structure-only outline (signatures, first docstring line, elided bodies) instead of a full file when the agent only needs shape | The existing `alfred-codegraph@1` index in `lib/code_graph.py` |
| **Delta read** | On a re-read of a file already surfaced this firing, return only a unified diff of what changed | A per-worktree read ledger in `lib/agent_runner/read_ledger.py` |

Both are deterministic and local. Neither can hide code the agent must edit (see
[Correctness guarantee](#correctness-guarantee)).

## Skeleton projection

A skeleton is a compact structural view of one source file. It keeps each
class/def signature and, for Python, the first line of its docstring, and
replaces every body with an explicit marker:

```
skeleton: app/service.py (python) - 2 symbol(s), bodies elided

[preamble: 4 line(s) elided]
class Service:
    """Handle inbound requests."""
    def handle(self, request):
        """Validate and dispatch."""
        [body: 12 line(s) elided]
```

It **reuses the code map's own symbol anchors** (`{"name", "line"}` per file)
rather than adding a parser or index. The projection is deterministic: identical
`(source, symbols)` inputs always render the same text, regardless of the order
symbols arrive from the index. Language coverage follows whatever
`bin/code-map-refresh.py` already parses (Python, TypeScript/JavaScript, Kotlin,
Go, Rust, Swift). Files with no indexed symbols fall back to a bounded head
slice so config and data files still yield a useful orientation view.

Entry points (`lib/code_graph.py`):

- `project_skeleton(path, source, *, symbols=None, language=None, ...)` -- pure,
  deterministic renderer. No disk access; ideal for tests.
- `skeleton_for_path(code_map, *, repo, path, repo_root, ...)` -- resolves a repo
  path against the code map, reads the on-disk source under `repo_root`, and
  returns a payload with the skeleton (or an empty skeleton plus a `reason` when
  the path is unmatched, ambiguous, or unreadable). The map is never modified.

## Delta reads

The first time a file is surfaced to a firing, the read ledger records its
content and returns it in full. On a re-read within the same firing:

- **unchanged** -- content is identical to the prior read; nothing is re-sent.
- **delta** -- content changed and a unified diff is smaller than the full file;
  the diff is returned and the ledger advances to the new content.
- **full** (fallback) -- the change is too large to diff usefully, the file is
  not usefully text, or no prior read exists; full content is returned.

State is **per-worktree and per-firing**: the ledger directory is keyed by the
firing id plus the worktree path, so two firings never share a cache. It always
lives at a single, firing-scoped location, `$ALFRED_HOME/state/read-ledger/<digest>/`.
Delta strictly requires `ALFRED_FIRING_ID`; without it the tool falls back to
full reads (which are always correct), so there is no configuration that could
let two firings share one ledger.

Entry points (`lib/agent_runner/read_ledger.py`): `ReadLedger.surface(key,
content, ...)` returns a `ReadResult` with the mode, reason, and byte counts.

## Wiring

The battery is exposed two ways on Claude-engine firings.

**Pull tools (default on)** -- added to the read-only `alfred_memory` MCP bridge
(`bin/alfred-mcp.py`):

- `alfred_code_skeleton(repo, path, symbol?)` -- an orientation outline of a
  file (or one symbol) from the code map.
- `alfred_read_delta(repo, path)` -- a delta-aware read backed by the ledger.

These are additive, read-only, and never replace the native `Read`/`Edit`
tools, so they cannot hide code.

**Push priming (default off)** -- `invoke_agent_engine(...,
orientation_paths=[...])` can prepend skeletons for a caller-supplied set of
orientation files. It is a no-op unless `ALFRED_SKELETON_PRIMING` is armed **and**
the caller passes orientation paths. The firing's edit-target is never passed
here.

## Configuration

Everything is config-driven (env), conservative by default. Nothing is
hardcoded at the call site.

| Env var | Default | Effect |
|---|---|---|
| `ALFRED_READ_DELTA` | on | Delta behavior for `alfred_read_delta`. Set falsy to always return full content. |
| `ALFRED_READ_DELTA_MAX_RATIO` | `0.5` | Emit a delta only when the diff is at most this fraction of the full file; otherwise full. |
| `ALFRED_READ_DELTA_CONTEXT` | `3` | Unchanged context lines each side of a change in the diff. |
| `ALFRED_READ_DELTA_MAX_CHARS` | `400000` | Above this size on either side, skip diffing and return full content. |
| `ALFRED_SKELETON_PRIMING` | off | Arm push priming of orientation skeletons into the prompt. |
| `ALFRED_SKELETON_MAX_FILES` | `6` | Max orientation files rendered per priming pass. |
| `ALFRED_SKELETON_MAX_SIGNATURE_LINES` | `6` | Max lines kept per signature before eliding. |

## Correctness guarantee

The distinction the battery preserves is **orientation versus edit-target**.

- **Skeletons are orientation, never an edit surface.** Every elided body is
  marked `[body: N line(s) elided]` and is one full `Read` of the real file
  away. The push-priming block says so in words, and it never skeletonizes the
  firing's edit-target. An agent that must change a file reads and edits the
  real file in full; the skeleton only helps it decide where to look.
- **Delta reads are loss-free.** The first read of any file is always full. A
  re-read returns a unified diff against exactly what was previously surfaced,
  so the prior copy plus the diff reconstruct the current file exactly. When a
  change is too large, non-textual, or otherwise not usefully diffable, the
  ledger falls back to full content. No information is ever dropped.

Because both paths are additive and the native `Read`/`Edit` tools are
untouched, the battery can save tokens on orientation and re-reads without ever
hiding code an agent needs to see or change.

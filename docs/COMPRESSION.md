# Compression engines

Verbose tool output is the biggest recurring token sink in an autonomous
firing. Alfred compacts that output at the Claude Code tool-I/O boundary before
it reaches the model (see [TOOL_COMPACTOR.md](TOOL_COMPACTOR.md) for the seam and
the safety valve). This doc covers the **engine selector**: which compressor
does the shrinking.

There are two engines, and the built-in one is the zero-install default.

| Engine | What it is | Install |
|---|---|---|
| **builtin** (default) | The pure-Python, stdlib-only #453 compactor (`lib/tool_compactor.py`): ANSI strip, de-dupe, head+tail budget, all-green test collapse. | Nothing. Ships with Alfred. |
| **headroom** | The optional [`headroom-ai`](https://pypi.org/project/headroom-ai/) engine (Apache-2.0 upstream): a more capable compressor for tool output, logs, and JSON. | `pipx install headroom-ai` (or autofetch, opt-in). |

A fresh solo install needs **nothing extra**. The built-in compactor is both the
default and the fallback, so headroom is never a hard dependency: when it is not
installed, every headroom path is a clean no-op that routes back to the built-in
compactor.

`headroom-ai` is Apache-2.0 upstream. Everything Alfred ships here is its own
glue around that public package (`lib/headroom_engine.py`,
`lib/compression_engine.py`); no upstream code is vendored.

## Selecting an engine

One environment variable, read at call time:

```sh
# in $ALFRED_HOME/.env
ALFRED_COMPRESSION_ENGINE=builtin   # default: the #453 compactor
ALFRED_COMPRESSION_ENGINE=headroom  # use headroom when available, else builtin
ALFRED_COMPRESSION_ENGINE=off       # disable compaction entirely (raw passthrough)
```

An unset or unrecognized value falls back to `builtin`, so a typo can never
silently disable compaction or route through an engine that is not there.

With the engine unset or `builtin`, behaviour is **byte-identical to today** -
this is a backward-compatible addition.

## How the headroom path behaves

When `ALFRED_COMPRESSION_ENGINE=headroom`:

1. **Availability check.** Alfred detects headroom (an importable `headroom`
   Python package, an `ALFRED_HEADROOM_BIN` override, or `headroom` on `PATH`).
   If nothing resolves, the engine falls back to the built-in compactor.
2. **Safety valve first.** Before headroom ever sees the text, the same
   confirmed-success valve the built-in compactor uses is applied
   (`tool_compactor.compaction_gate`). An errored, unknown-status, disabled,
   untargeted, or too-small output is passed through **untouched** and headroom
   is never invoked, so an error can never be hidden - regardless of engine.
3. **Compress, or fall back.** On a confirmed-success, over-budget Bash output,
   headroom compresses it. If headroom declines (returns nothing) or produces no
   saving, Alfred falls back to the deterministic built-in compactor so the
   token win is not lost.

### No-op when absent

Detection and compression never raise. With headroom uninstalled:

- `ALFRED_COMPRESSION_ENGINE=headroom` behaves exactly like `builtin`.
- The compression benchmark marks the headroom arm `not-run` (never a fabricated
  number - see [BENCHMARKS.md](BENCHMARKS.md)).

## Configuration

All knobs are environment variables; set them in `$ALFRED_HOME/.env`. Defaults
work out of the box, and the headroom knobs are inert unless the engine is set to
`headroom`.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_COMPRESSION_ENGINE` | `builtin` | Engine selector: `builtin` \| `headroom` \| `off`. |
| `ALFRED_HEADROOM_BIN` | (unset) | Explicit path to a `headroom` CLI binary. Skips `PATH` lookup. |
| `ALFRED_HEADROOM_MODEL` | (unset) | Model id passed to `headroom.compress(...)` for its tokenizer. Unset lets headroom pick its own default. |
| `ALFRED_HEADROOM_MESSAGE_ROLE` | `user` | Role for the message carrying the tool output to headroom. headroom auto-detects compressible content, so no marker is required; override to `tool` if you want to signal it explicitly. |
| `ALFRED_HEADROOM_COMPRESS_CMD` | (unset) | For a CLI-only install: the command that compresses stdin to stdout (`{bin}` is substituted; the template is `shlex`-split so quoted args survive). Unset means "library path only". |
| `ALFRED_HEADROOM_AUTOFETCH` | `0` (off) | Opt-in install of headroom-ai, run **only out-of-band** (an explicit `alfred` setup step), never inline in the hook path. **Off by default for a strict no-network install** - Alfred never installs anything without this flag. |
| `ALFRED_HEADROOM_AUTOFETCH_CMD` | `pipx install headroom-ai` | The install command autofetch runs when enabled (`shlex`-split, so quoted args like `"headroom-ai[all]"` survive). |

The built-in compactor's own knobs (`ALFRED_OUTPUT_COMPACTOR*`) still apply as
the byte-budget and targeting gate for **both** engines - see
[TOOL_COMPACTOR.md](TOOL_COMPACTOR.md).

### Autofetch is out-of-band, never on the hook path

Installing a package shells out and can block for many seconds. The PostToolUse
compaction path therefore **never** installs headroom inline - doing so would
hang the agent's tool call. When headroom is absent the selector falls straight
back to the built-in compactor. Autofetch (`ALFRED_HEADROOM_AUTOFETCH`) is run
only out-of-band, from an explicit `alfred` setup/init step, and at most once per
process.

### Autofetch and the importable-vs-CLI distinction

`pipx install headroom-ai` puts the `headroom` **CLI** on your `PATH` in an
isolated venv. To use headroom as Alfred's **library** compressor (the primary
runtime path), install it so it is importable by the interpreter that runs the
hook - for example `pip install headroom-ai` (or `uv pip install headroom-ai`)
into Alfred's environment, or set `ALFRED_HEADROOM_AUTOFETCH_CMD` accordingly.
A CLI-only install compresses only when you also set
`ALFRED_HEADROOM_COMPRESS_CMD`, since `headroom-ai`'s CLI is oriented at
wrapping/proxying an agent rather than a documented "compress this blob"
subcommand, and Alfred does not invent one.

The library path returns headroom's `CompressResult` object (compressed
`.messages` plus `.tokens_saved` / `.compression_ratio`); Alfred unwraps the
compressed text from it and only falls back to the built-in compactor when
headroom genuinely returned nothing usable.

## Model-derived thresholds

The compactor's byte budget (`min_bytes` = the size an output must exceed before
compaction fires, `max_bytes` = the target size of the compacted result) now
**defaults to a fraction of the active model's context window** instead of a
fixed constant (`lib/model_context.py`). A large-window model can afford more
inline tool output before compacting; a small one should compact sooner. The
window is read from the firing's env at hook time - the runner exports
`ALFRED_ACTIVE_MODEL` / `ALFRED_ACTIVE_ENGINE` into the subprocess env (the
`--model` alias is a CLI arg, not an inherited var, so it is surfaced there).

The fractions are chosen so the baseline **200K-token window reproduces the
historical fixed defaults exactly** (2000 / 8000 bytes), and a larger window
scales up proportionally (a 1M window yields 10000 / 40000). Detection falls back
conservatively: an undetectable model uses the smallest common Claude window, so
an unknown model never inflates the budget past today's.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_COMPACTION_MODEL` | (unset) | Override the model used to derive the budget (else `ALFRED_ACTIVE_MODEL` / `ANTHROPIC_MODEL`, or `ALFRED_CODEX_MODEL` on the Codex engine). |
| `ALFRED_COMPACTION_CONTEXT_TOKENS` | (unset) | Override the window directly, in tokens, bypassing the model table (for a model Alfred does not yet know, or for tuning). |

The existing `ALFRED_OUTPUT_COMPACTOR_MIN_BYTES` / `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES`
overrides still work and **win over the derived value** - the derived value is
only the new default.

## Offload oversized tool output to a re-readable path

When a successful tool output is large enough to compact, Alfred no longer ships
only the truncated head+tail. It writes the **full** output to a firing-scoped
scratch file and inlines a head/tail preview plus the absolute path, so the agent
can re-read the exact omitted slice (a line range of the saved file) instead of
re-running the command (`lib/tool_offload.py`, borrowing the shape of deepagents'
`_message_eviction`). The saved copy lives at:

```
$ALFRED_HOME/state/firings/<firing_id>/tool-output/<n>.txt
```

The `<firing_id>` is `ALFRED_FIRING_ID` (exported by the runner) or the Claude
Code session id. Each file's index is claimed atomically (`O_CREAT | O_EXCL`),
so two concurrent hook processes offloading for the same firing can never
overwrite each other's output. The inline preview honours the **same compaction
byte budget it replaces** (the `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES` override, else
the model-derived default), so a log made of a few very long lines can never ride
back into the context nearly in full. Offload is a **best-effort enhancement**:
it only runs on output the confirmed-success valve already cleared for
compaction, and any failure (disk bound breached, unwritable path) falls straight
back to the compactor's own head+tail output - the token win is never lost, and
an error is never hidden.

Total offloaded disk **per firing is bounded** (`ALFRED_TOOL_OFFLOAD_MAX_BYTES`,
default ~50MB); once a firing's directory would exceed the bound, further outputs
skip offload. Expired `tool-output/` subtrees are swept by the existing daily
cleanup (`bin/agent-cleanup.py`, retention `ALFRED_FIRINGS_RETENTION_DAYS`,
default 30d; 1d under emergency disk pressure). The sweep removes only the
`tool-output/` directory offload owns; the parent `state/firings/<id>/` dir is
reaped only when empty, so sibling firing-scoped state survives.

| Variable | Default | What it does |
|---|---|---|
| `ALFRED_TOOL_OFFLOAD` | `1` (on) | Offload oversized tool output to a file; opt out with `0` (falls back to inline compaction). |
| `ALFRED_TOOL_OFFLOAD_MAX_BYTES` | `50000000` | Per-firing disk bound for offloaded output. |
| `ALFRED_TOOL_OFFLOAD_PREVIEW_HEAD_LINES` | `20` | Head lines kept inline alongside the saved-path pointer. |
| `ALFRED_TOOL_OFFLOAD_PREVIEW_TAIL_LINES` | `20` | Tail lines kept inline alongside the saved-path pointer. |

## The hook stays stdlib-only

The built-in path keeps its stdlib-only guarantee: it runs on the Claude Code
hook path under any `python3` without the project venv. The headroom package is
imported **dynamically** (`importlib`), and only when the engine is set to
`headroom` and headroom is present, so the hook modules never carry a static
non-stdlib import.

## Measuring it

The compression benchmark runs the same real tool-output payloads (grep, JSON,
logs) through both engines and reports the token-reduction ratio for each:

```sh
alfred benchmark compression            # human-readable table
alfred benchmark compression --json     # machine-readable
```

See [BENCHMARKS.md](BENCHMARKS.md#compression-builtin-453-vs-headroom) for what
it measures and how it reports honestly when headroom is not installed.

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
| `ALFRED_HEADROOM_COMPRESS_CMD` | (unset) | For a CLI-only install: the command that compresses stdin to stdout (`{bin}` is substituted). Unset means "library path only". |
| `ALFRED_HEADROOM_AUTOFETCH` | `0` (off) | Opt-in one-time install of headroom-ai on first use. **Off by default for a strict no-network install** - Alfred never installs anything without this flag. |
| `ALFRED_HEADROOM_AUTOFETCH_CMD` | `pipx install headroom-ai` | The install command autofetch runs when enabled. |

The built-in compactor's own knobs (`ALFRED_OUTPUT_COMPACTOR*`) still apply as
the byte-budget and targeting gate for **both** engines - see
[TOOL_COMPACTOR.md](TOOL_COMPACTOR.md).

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

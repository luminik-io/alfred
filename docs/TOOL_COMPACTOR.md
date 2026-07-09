# Tool-output compactor

The single biggest recurring token sink in an autonomous firing is verbose tool
output: thousand-line build logs, `npm install` chatter, all-green test runs,
progress spinners, and ANSI colour codes. Every byte of that re-enters the
model's context and is paid for on every turn. Alfred intercepts it at the Claude
Code tool-I/O boundary and shrinks it before it reaches the model.

This battery extends the same hook seam as the deterministic guardrails in
[`lib/alfred_hooks.py`](../lib/alfred_hooks.py). The compaction logic lives in
[`lib/tool_compactor.py`](../lib/tool_compactor.py). It is deterministic and
byte-budget driven, mirroring the prompt
[context governor](../lib/agent_runner/context_governor.py); there is no LLM call
and no summarization that invents facts.

## The seam

**PostToolUse output compactor.** After a Bash command runs and its exit code
confirms success (see the safety valve below), its output is ANSI-stripped,
de-duplicated (runs of identical lines collapse to `line (xN)`), progress
spinners are flattened, and the result is bounded to a byte budget as a
head-plus-tail excerpt with an explicit `[ALFRED_OUTPUT_COMPACTOR omitted_lines=N]`
marker. The compact form is returned to Claude Code as
`hookSpecificOutput.updatedToolOutput`, so the model never sees the raw blob.

A run that is **purely** a test run (every line is test-runner output ending in
an all-green footer) is collapsed further, to just its counts line, since the
thousands of `PASSED` lines carry no signal. This aggressive shortcut is gated on
the output being nothing but a test run: a *mixed* success such as
`git fetch && pytest`, where useful non-test output precedes the footer, falls
back to the normal head-plus-tail compaction so that non-test output is preserved
rather than discarded.

## Why there is no command normalizer

An earlier draft of this battery also carried a PreToolUse *command normalizer*: a
small allowlist that rewrote verbose commands to quiet equivalents (for example
`git status` to `git status --short --branch`, or adding `--quiet` to
`git pull` / `git fetch`). It was **intentionally dropped**.

No `git` command rewrite proved reliably output-equivalent. `git status --short`
drops the submodule summary when `status.submoduleSummary` is set; `git pull
--quiet` drops the merge or fast-forward summary; `git fetch --quiet` drops the
new-branch, tag, and ref-update summaries that Git normally prints. Each is the
same class of problem: the "quiet" form silently omits output the agent may need.
Rather than chase edge cases with ever-cleverer detection, the battery keeps only
the safe half. Compacting output that has *already been produced* is safe because
the safety valve below only compacts on a confirmed success; rewriting a command
up front to suppress output it has not produced yet is not.

## Safety valve: compact only on confirmed success

Compaction must never hide an error, so it is gated on **proof of success**, not
on the absence of a known error signature. The compactor reads the structured
exit status of the tool response and applies one rule:

- **Confirmed success** (structured exit code `== 0`): the output is eligible for
  compaction.
- **Confirmed failure** (structured exit code `!= 0`, or a structured
  `interrupted` / `is_error` flag): the full output is teed through untouched.
- **Unknown status** (a plain-string response with no exit code, or a structured
  response carrying no exit status at all): the full output passes through
  untouched. Success is never inferred, so an unrecognized error format cannot be
  hidden.

This inverts the naive "compact unless it looks like a failure" approach, which
can never enumerate every error format (a Python traceback, `fatal:`, `npm ERR!`,
`make: *** No rule to make target...`, and so on). By requiring a positive
exit-code-0 signal, an error in any format the compactor has never seen still
reaches the model in full, because it was never proven successful. The trade is
deliberate: on unknown status the compactor saves no tokens rather than risk
hiding an error.

The whole path is also **fail-conservative**: on any ambiguity or parse problem
the original bytes are returned unchanged. The worst case is fewer tokens saved,
never a hidden error.

## Pluggable engines (builtin vs headroom)

The compaction above is the **built-in** engine: pure-Python, stdlib-only, the
zero-install default. Alfred also ships an optional, more capable engine,
[`headroom-ai`](https://pypi.org/project/headroom-ai/) (Apache-2.0 upstream),
behind a config-driven selector:

```sh
ALFRED_COMPRESSION_ENGINE=builtin   # default: this compactor
ALFRED_COMPRESSION_ENGINE=headroom  # route through headroom when available
ALFRED_COMPRESSION_ENGINE=off       # disable compaction entirely
```

The built-in compactor stays the default **and** the fallback: headroom is never
a hard dependency, and when it is not installed the headroom setting behaves
exactly like `builtin`. Crucially, the confirmed-success safety valve below is
enforced identically no matter which engine runs - an errored or unknown-status
output is teed through untouched before any engine sees it, so an error is never
hidden. See [COMPRESSION.md](COMPRESSION.md) for the selector, headroom install,
and the no-op-when-absent behaviour.

## Enabling the battery

The compactor rides the same opt-in flag as the guardrail hook. It is attached
only when `ALFRED_AGENT_HOOKS=1` (unattended autonomy stays the default, so the
hook is off unless you ask for it).

## Configuration knobs

All knobs are read at call time from the environment (12-factor), so an operator
can override them in production without a redeploy.

| Variable | Default | Effect |
| --- | --- | --- |
| `ALFRED_AGENT_HOOKS` | off | Master opt-in that wires the PreToolUse guardrails and the PostToolUse compactor. |
| `ALFRED_COMPRESSION_ENGINE` | `builtin` | Engine selector: `builtin` (this compactor) \| `headroom` (optional engine, else builtin) \| `off`. See [COMPRESSION.md](COMPRESSION.md). |
| `ALFRED_OUTPUT_COMPACTOR` | on | Set to `0` to disable output compaction (raw output passes through). Applies to both engines. |
| `ALFRED_OUTPUT_COMPACTOR_TOOLS` | `Bash` | Comma-separated list of tools whose output is compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MIN_BYTES` | `2000` | Output smaller than this passes through un-compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES` | `8000` | Target byte budget for a compacted result. |
| `ALFRED_OUTPUT_COMPACTOR_HEAD_LINES` | `40` | Lines kept from the head of a compacted excerpt. |
| `ALFRED_OUTPUT_COMPACTOR_TAIL_LINES` | `40` | Lines kept from the tail of a compacted excerpt. |

The compactor stays stdlib-only so it runs on the Claude Code hook path under any
`python3` without the project venv, exactly like the guardrail hook it sits beside.

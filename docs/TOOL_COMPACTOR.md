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

**PostToolUse output compactor.** After a Bash command runs, its output is
ANSI-stripped, de-duplicated (runs of identical lines collapse to `line (xN)`),
progress spinners are flattened, and the result is bounded to a byte budget as a
head-plus-tail excerpt with an explicit `[ALFRED_OUTPUT_COMPACTOR omitted_lines=N]`
marker. An all-green test run is reduced to its counts line. The compact form is
returned to Claude Code as `hookSpecificOutput.updatedToolOutput`, so the model
never sees the raw blob.

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
the tee-on-failure valve below guarantees an error is never hidden; rewriting a
command up front to suppress output it has not produced yet is not.

## Safety valve: tee the full output on failure

Compaction must never hide an error. If a command **failed**, the full output is
passed through untouched:

- A non-zero exit code always tees the full output.
- When the exit code is unknown (a plain-string tool response carries no exit
  code), an error signature (a Python traceback, `fatal:`, `npm ERR!`, a segfault,
  a bare `Error` / `FAILED` / `Exception` token) or a failing test tail
  (`1 failed, ...`) also tees the full output.

So the compactor only ever shrinks the boring, successful logs. A traceback, a
build error, or a test failure always reaches the model in full. This is the
tee-full-output-on-failure pattern; see `looks_like_failure` and its tests in
[`tests/test_tool_compactor.py`](../tests/test_tool_compactor.py).

The whole path is also **fail-conservative**: on any ambiguity or parse problem
the original bytes are returned unchanged. The worst case is fewer tokens saved,
never a hidden error.

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
| `ALFRED_OUTPUT_COMPACTOR` | on | Set to `0` to disable output compaction (raw output passes through). |
| `ALFRED_OUTPUT_COMPACTOR_TOOLS` | `Bash` | Comma-separated list of tools whose output is compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MIN_BYTES` | `2000` | Output smaller than this passes through un-compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES` | `8000` | Target byte budget for a compacted result. |
| `ALFRED_OUTPUT_COMPACTOR_HEAD_LINES` | `40` | Lines kept from the head of a compacted excerpt. |
| `ALFRED_OUTPUT_COMPACTOR_TAIL_LINES` | `40` | Lines kept from the tail of a compacted excerpt. |

The compactor stays stdlib-only so it runs on the Claude Code hook path under any
`python3` without the project venv, exactly like the guardrail hook it sits beside.

# Tool-output compactor and command normalizer

The single biggest recurring token sink in an autonomous firing is verbose tool
output: thousand-line build logs, `npm install` chatter, all-green test runs,
progress spinners, and ANSI colour codes. Every byte of that re-enters the
model's context and is paid for on every turn. Alfred intercepts it at the Claude
Code tool-I/O boundary and shrinks it before it reaches the model, and rewrites a
small allowlist of verbose commands into their quiet equivalents before they run.

This battery extends the same hook seam as the deterministic guardrails in
[`lib/alfred_hooks.py`](../lib/alfred_hooks.py). The compaction and normalization
logic lives in [`lib/tool_compactor.py`](../lib/tool_compactor.py). It is
deterministic and byte-budget driven, mirroring the prompt
[context governor](../lib/agent_runner/context_governor.py); there is no LLM call
and no summarization that invents facts.

## The two seams

- **PostToolUse output compactor.** After a Bash command runs, its output is
  ANSI-stripped, de-duplicated (runs of identical lines collapse to `line (xN)`),
  progress spinners are flattened, and the result is bounded to a byte budget as a
  head-plus-tail excerpt with an explicit `[ALFRED_OUTPUT_COMPACTOR omitted_lines=N]`
  marker. An all-green test run is reduced to its counts line. The compact form is
  returned to Claude Code as `hookSpecificOutput.updatedToolOutput`, so the model
  never sees the raw blob.

- **PreToolUse command normalizer.** Before a Bash command runs, an allowlisted
  rewrite table swaps a verbose command for a quiet equivalent that is strictly
  output-equivalent (it changes only transfer or progress chatter, never the result
  the agent needs). The rewrite is returned as `hookSpecificOutput.updatedInput`.
  Current allowlist:

  | Command | Rewritten to | Why it is safe |
  | --- | --- | --- |
  | `git fetch` / `git clone` | same command with `--quiet` | Pure transfer commands whose only suppressed output is download progress. They have no merge or working-tree summary to hide, and errors still print. |

  The allowlist is kept deliberately small, and a rewrite is left off entirely
  rather than guarded by clever detection when it is not always equivalent:

  - `git status` is **not** rewritten to `--short --branch`: under
    `status.submoduleSummary` the long form emits a submodule summary the short
    form drops, so the two are not always output-equivalent.
  - `git pull` is **not** given `--quiet`: it also merges, and `--quiet` would
    swallow the merge or fast-forward summary the user needs.

  The normalizer also refuses to touch any command containing a shell
  metacharacter (pipe, redirect, `&&`, `$(...)`), because a rewrite could change
  what a downstream `grep` or `awk` parses. Anything not on the allowlist passes
  through verbatim.

## Safety valve: tee the full output on failure

Compaction must never hide an error. If a command **failed**, the full output is
passed through untouched:

- A non-zero exit code always tees the full output.
- When the exit code is unknown, an error signature (a Python traceback, `fatal:`,
  `npm ERR!`, a segfault, `command not found`) or a failing test tail
  (`1 failed, ...`) also tees the full output.

So the compactor only ever shrinks the boring, successful logs. A traceback, a
build error, or a test failure always reaches the model in full. This is the
tee-full-output-on-failure pattern; see `looks_like_failure` and its tests in
[`tests/test_tool_compactor.py`](../tests/test_tool_compactor.py).

The whole path is also **fail-conservative**: on any ambiguity or parse problem
the original bytes are returned unchanged. The worst case is fewer tokens saved,
never a hidden error.

## Enabling the battery

Both seams ride the same opt-in flag as the guardrail hook. They are attached only
when `ALFRED_AGENT_HOOKS=1` (unattended autonomy stays the default, so the hook is
off unless you ask for it). Within that, each half is individually tunable.

## Configuration knobs

All knobs are read at call time from the environment (12-factor), so an operator
can override them in production without a redeploy.

| Variable | Default | Effect |
| --- | --- | --- |
| `ALFRED_AGENT_HOOKS` | off | Master opt-in that wires both the PreToolUse and PostToolUse hooks. |
| `ALFRED_OUTPUT_COMPACTOR` | on | Set to `0` to disable output compaction (raw output passes through). |
| `ALFRED_OUTPUT_COMPACTOR_TOOLS` | `Bash` | Comma-separated list of tools whose output is compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MIN_BYTES` | `2000` | Output smaller than this passes through un-compacted. |
| `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES` | `8000` | Target byte budget for a compacted result. |
| `ALFRED_OUTPUT_COMPACTOR_HEAD_LINES` | `40` | Lines kept from the head of a compacted excerpt. |
| `ALFRED_OUTPUT_COMPACTOR_TAIL_LINES` | `40` | Lines kept from the tail of a compacted excerpt. |
| `ALFRED_CMD_NORMALIZER` | on | Set to `0` to disable the PreToolUse command rewrite. |

The compactor stays stdlib-only so it runs on the Claude Code hook path under any
`python3` without the project venv, exactly like the guardrail hook it sits beside.

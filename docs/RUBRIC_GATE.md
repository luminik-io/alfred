# Rubric grade-then-revise gate

The rubric gate is an optional self-check on the BUILD step. Before an
implementer opens a PR, a cheap separate grader reads the committed diff against
a rubric derived from the issue and returns a structured verdict. On
`needs_revision` the implementer is re-dispatched once with the gaps, then the
diff is regraded. Whatever the final verdict, the PR opens and the verdict is
shown in the PR body. The gate improves the change; it never blocks it.

It is OFF by default. Turn it on with `ALFRED_RUBRIC_GATE=1` once you have
benchmarked it on your own repos.

## Why

An implementer run can finish and commit while still missing something the issue
asked for: a test, a doc update, an edge case. A backward-looking review catches
this later. The rubric gate catches it earlier, at the seam between build and PR,
where a single extra revision pass is cheap and the fix is still in the agent's
context. The idea is borrowed from rubric-graded agent loops; the implementation
is native to Alfred's runtime and imports no third-party agent framework.

## How it works

1. Derive the rubric (`derive_rubric` in `lib/agent_runner/rubric.py`). The
   source of truth is the issue's own acceptance criteria (an explicit
   `## Acceptance criteria` section, else the first checkbox list). When the
   issue carries none, a generic engineering rubric is used: tests pass, scope
   matches the issue, no unrelated changes, docs updated if user-facing. The
   rubric is bounded to six criteria.
2. Grade the committed diff. A separate cheap read-only grader engine (Codex by
   default, `ALFRED_RUBRIC_GRADER_ENGINE` to override) reads the diff plus the
   rubric and returns a per-criterion verdict as JSON. Only the diff and the
   rubric reach the grader, not the whole run transcript. The grader output is
   untrusted: any malformed, empty, or non-conforming response degrades to a
   terminal `grader_error` verdict rather than green-lighting the change.
3. Revise on `needs_revision`. The implementer is re-dispatched in the same
   worktree with the failed criteria appended as feedback, up to
   `ALFRED_RUBRIC_MAX_ITERATIONS` times (default 1). Each revision commit lands
   on the branch before the push. The diff is regraded after each pass.
4. Open the PR and record the verdict. On `satisfied`, `failed`, a grader error,
   or exhausted iterations, the PR opens and the final rubric verdict is
   rendered into the PR body under `## Rubric grade`, with failed criteria shown
   plainly. Nothing is hidden.

The gate is fail-open: a broken grader, an unrunnable revision, or any gate
error degrades to opening the PR without a rubric section, never to derailing a
ready change.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALFRED_RUBRIC_GATE` | `0` | Enable the gate. Off until you have benchmarked it. |
| `ALFRED_RUBRIC_MAX_ITERATIONS` | `1` | Max revision passes before the PR opens (1 to 10). |
| `ALFRED_RUBRIC_GRADER_ENGINE` | Codex | Engine used for the read-only grade. |

The gate never runs in dry-run, and it skips grading when the diff is empty.

## Related

- [`VERIFICATION.md`](VERIFICATION.md): the `## Verification evidence` block,
  the backward-looking companion to this forward-looking gate.
- [`ENGINE_ROUTING.md`](ENGINE_ROUTING.md): how the primary and grader engines
  are chosen on independent axes.
- `lib/agent_runner/rubric.py`: the grading primitives and the loop.
- `bin/senior-dev.py`: where the gate is wired into the build-to-PR seam.

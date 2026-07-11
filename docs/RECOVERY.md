# Failure auto-recovery

When an agent firing does the work but stumbles on the last step, the fix is
usually small: a formatter to run, a rebase onto a moved base, a test to green.
Auto-recovery gives that step one bounded, self-healing attempt before the
firing halts and holds the issue for a human.

The engine turn that authored the change is the same engine that repairs it.
There is one dispatcher, in [`lib/agent_runner/recovery.py`](../lib/agent_runner/recovery.py),
and it reads the failure text and nothing else.

## When it runs

At the push step of a firing. After the engine has committed real work,
senior-dev runs the repo's pre-push checks (lint, compile, tests), validates any
changed workflows, and pushes the branch. If that step fails, the captured
stderr or log excerpt is classified. For a recoverable class, one bounded
recovery turn runs in the same worktree with an instruction to fix the cause and
re-push. Only if that turn cannot heal the failure does the firing fall back to
its existing behaviour: preserve the local work on a recovery ref, release the
issue for retry, and post a warning.

## What it recovers, and what it never touches

Four failure classes are recoverable, because a bounded turn can fix them:

- **Lint or format hook rejection.** A pre-commit or pre-push hook (ruff,
  eslint, prettier, black, a husky hook) rejected the change. The turn runs the
  formatter, fixes the lint, and re-pushes.
- **Non-fast-forward or conflict.** The remote moved, so the push was rejected,
  or a rebase hit a conflict. The turn rebases onto the updated base, resolves
  the conflict, and re-pushes.
- **Failing CI check.** A compile, type-check, or test step failed. The turn
  reproduces it locally, fixes the root cause, and re-pushes.
- **Transient network.** A timeout, connection reset, or a 5xx from the remote.
  The turn confirms the tree is intact and re-pushes.

Three classes are never recovered. They fall straight to hold:

- **Approval-gate denial.** The change is waiting on a human decision (not
  approved, unresolved review threads, changes requested). Recovering would
  bypass a person.
- **Scrub-check rejection.** The public/private scrub gate found a home path, an
  internal name, or a secret. A turn must not paper over a boundary violation.
- **Auth error.** Bad or missing credentials (401 or 403, permission denied,
  authentication failed). A turn cannot mint credentials.

Classification tests the never-recover classes first, so a failure that mentions
both an auth error and a push rejection is held, not retried. Anything the
classifier does not recognise is treated as never-recover, so an unfamiliar
failure fails closed to hold rather than burning a turn on a guess.

## The attempt cap

`ALFRED_RECOVERY_MAX_ATTEMPTS` sets how many bounded recovery turns may run
before the firing holds. The default is `1`. Setting it to `0` disables recovery
entirely, and the push step behaves exactly as it did before this existed. The
value is clamped to a small ceiling so a misconfigured value cannot spawn a long
chain of paid turns. See [`CONFIG.md`](CONFIG.md).

## Telemetry

Every dispatch emits a distinct firing event so proof and telemetry can count
self-healed runs. `recovery_attempted` fires before each turn, `recovery_succeeded`
when a turn heals the failure, `recovery_exhausted` when every attempt fails, and
`recovery_skipped` when the failure is a never-recover class or recovery is
disabled. A `[RECOVERED]` line is also logged and posted to Slack when a firing
heals itself.

## Related

- [`MERGE_GATE.md`](MERGE_GATE.md): the separate GitHub-native gate that decides
  when a PR may merge. Recovery repairs the firing's own push step; the merge
  gate remains fail-closed and never merges an unready PR.
- [`STATE_MACHINE.md`](STATE_MACHINE.md): the issue claim lifecycle recovery
  falls back into when a turn cannot heal the failure.

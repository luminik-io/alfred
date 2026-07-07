# Verification evidence on agent PRs

Every agent-authored pull request should carry evidence a non-author can check
at a glance. Human reviewers and other agents should not have to trust the
author's word that the change works; the PR itself should show what ran and
what it proved.

Alfred attaches a `## Verification evidence` block to the PR body. It has three
tiers, in order of how much a reviewer can rely on them:

1. **Test evidence** (objective). The pre-push check the runner already ran,
   with its pass/fail summary and command. This is the strongest signal
   because it is machine output the agent did not write.
2. **Diff evidence** (objective). A files/lines summary of the branch.
3. **Acceptance-criteria self-assessment** (a claim to check, not proof). The
   implementing engine reviews its own diff against the issue's acceptance
   criteria and reports which it believes are met. Clearly labelled as
   self-reported.
4. **Screenshot evidence** (optional, opt-in per repo). Before/after
   screenshots of a configured route, committed to the PR branch.

The block is honest by construction: anything that could not be generated is
rendered as `not captured (<reason>)`. Evidence is never silently omitted and
never fabricated.

## What a block looks like

```markdown
## Verification evidence

### Tests
- Pre-push checks passed - pytest: 128 passed, 2 skipped in 41.3s
- Command: `uv run ruff check . && uv run mypy . && uv run pytest`

### Diff
- 3 file(s) changed, +142 / -18 lines
  - `lib/retry.py`
  - `tests/test_retry.py`
  - `docs/RETRY.md`

### Acceptance criteria (engine self-assessment)
> Self-reported by the implementing engine reviewing its own diff. This is a
> claim to check, not an independent verification.
- [x] Failed checkout calls retry up to 3 times - added backoff loop in retry.py
- [x] Retries are logged - structured log added
- [ ] Retry count is configurable - not implemented in this diff

### Screenshots
- Route: `/dashboard`
- Before: [`.alfred/evidence/senior-dev-20260703-abc/before.png`](.alfred/evidence/senior-dev-20260703-abc/before.png)
- After: [`.alfred/evidence/senior-dev-20260703-abc/after.png`](.alfred/evidence/senior-dev-20260703-abc/after.png)
```

## Enabling and disabling

Test evidence and the self-assessment are **on by default**. The gate is a
single environment variable read by the runner:

```bash
ALFRED_PR_EVIDENCE=1   # default; attach test + diff + self-assessment
ALFRED_PR_EVIDENCE=0   # off; the test, diff, and self-assessment tiers are omitted
```

Screenshots are **strictly opt-in** and governed separately by per-repo config
(below), independent of `ALFRED_PR_EVIDENCE`. Turning the flag off on a repo
that has a preview configured still captures screenshots; the PR then carries
a screenshots-only evidence block. With the flag off and no preview
configured, the PR body has no evidence block at all.

The self-assessment tier makes one extra engine call per PR. Its turns and
cost are recorded against the same daily spend caps as the implementation
call, and the operator can bound it with `ALFRED_SENIOR_DEV_SELFASSESS_MAX_TURNS`.

## Test and diff evidence

The runner already runs each repo's pre-push command (see the pre-push section
of the agent config). Previously that output was discarded once the checks
passed. Now the runner captures it, extracts a one-line summary (pytest,
jest/vitest, and gradle are recognised; other runners fall back to the exit
code), and formats it into the block.

- If a repo has no pre-push command configured, the Tests section says
  `not captured (no pre-push command configured for this repo)`. That is
  honest: nothing ran, so nothing is claimed.
- If the checks failed, the PR does not open at all (the runner preserves the
  work and releases the issue). A failed-check block only appears on the WIP
  salvage path.

Diff evidence is a `git diff --numstat` summary against the base branch. It
never fails the PR.

## Acceptance-criteria self-assessment

The runner extracts acceptance criteria from the issue body. It prefers an
explicit section:

```markdown
## Acceptance criteria
- [ ] Failed checkout calls retry up to 3 times
- [ ] Retries are logged
- [ ] Retry count is configurable
```

If there is no such section, it falls back to the first checkbox list anywhere
in the issue.

The implementing engine is then asked to review **its own diff** against those
criteria and return a small JSON verdict. Each criterion is rendered as:

- `[x]` the engine claims the diff satisfies it,
- `[ ]` the engine says it does not,
- `[?]` the engine did not judge it (kept honest rather than assumed met).

This tier is explicitly labelled self-reported. It is a fast orientation aid
for a reviewer ("the author thinks these three are done, this one is not"), not
a substitute for review. When the engine returns nothing parseable, the section
says `not captured (engine did not return a parseable self-assessment)` and
still lists the criteria as unjudged.

## Screenshot evidence (opt-in)

For UI work, a picture is the fastest evidence. When a repo declares a preview
command, the runner captures a real before/after pair of a configured route:
the **after** shot on the PR-branch worktree, and the **before** shot on a
throwaway `git worktree` checked out at the PR's base ref. Both PNGs are
committed under `.alfred/evidence/<firing-id>/` on the PR branch and linked
from the body with relative paths so they render in the PR. When the base
checkout cannot be prepared, the before-image is reported as not captured
rather than faked.

Alfred does **not** bundle a browser or add a heavyweight mandatory dependency
to the core. You declare the screenshot command; the documented default shells
out to Playwright's one-shot screenshot subcommand, which downloads on demand:

```
npx --yes playwright screenshot --wait-for-timeout 1500 {url} {out}
```

`{url}` and `{out}` are substituted by the runner. Any command that writes a
PNG to `{out}` works (Puppeteer, `shot-scraper`, a project script, and so on).

### Per-repo config

Screenshots are configured in the agent TOML at
`$ALFRED_HOME/agents/<codename>.toml` under a `[preview.<repo>]` table:

```toml
[preview.your-frontend]
start_cmd   = "npm run dev"           # how to start the app (required)
url         = "http://localhost:5173" # base URL (required)
route       = "/dashboard"            # route to screenshot (default "/")
ready_regex = "Local:"                # optional: readiness marker in server output
screenshot_cmd = "npx --yes playwright screenshot {url} {out}"  # optional; default above
```

Readiness: when `ready_regex` is set, the runner polls the preview server's
output until the pattern appears (up to the boot timeout) before taking the
shot; when it is not set, a fixed grace period is used. A server that never
signals ready yields an honest `not captured` line rather than a blank or
stale image.

A repo with no `[preview.<repo>]` table (or missing `start_cmd`/`url`) never
attempts screenshots, and the Screenshots section is omitted entirely rather
than shown as "not captured" - absence is not dishonest when the feature was
never requested for that repo.

### Failure is reported, never faked

If the preview server does not start, the route does not load, or the
screenshot command fails, the block says so:

```markdown
### Screenshots
- Route: `/dashboard`
- _not captured_ (preview server did not become ready)
```

The PR still opens. Screenshot capture is best-effort and never blocks a change
that already passed its real checks.

## Design notes

- The formatting and parsing live in
  [`../lib/verification_evidence.py`](../lib/verification_evidence.py) with no
  runner coupling, so they are unit-tested against stubbed inputs. Every
  external command (screenshot, engine call) is injectable; the tests never
  launch a browser or an LLM.
- The runner glue lives in the PR-create path of the senior-dev role
  ([`../bin/senior-dev.py`](../bin/senior-dev.py)). The senior-dev is wired first; the same
  helpers can be reused by other agents that open PRs.
- Evidence generation is wrapped so it can never block a PR: any error inside
  it degrades to an honest note in the block, and the PR opens regardless.

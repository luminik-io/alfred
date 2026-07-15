# Merge gate

Alfred merges a pull request only when GitHub itself says it is ready. There is
one predicate, in [`lib/merge_gate.py`](../lib/merge_gate.py), and it reads
GitHub's own machinery. By default the reviewers are whoever GitHub says
approved the PR, with no reviewer names or review products hard-coded. When you
configure named external reviews, the gate also checks those specific bots by
their exact GitHub identities and looks for their exact clean-verdict text.

## The gate

A PR is mergeable by Alfred only when all of these hold:

1. The PR is open.
2. GitHub does not report `CHANGES_REQUESTED` or `REVIEW_REQUIRED`, and Alfred
   counts at least `ALFRED_MERGE_MIN_APPROVALS` distinct approvals on the exact
   current head. On a protected repo, GitHub independently enforces any stricter
   branch rule or code-owner requirement.
3. There are zero unresolved review threads, from any author.
4. `mergeStateStatus` is `CLEAN` and `mergeable` is `MERGEABLE`. This is
   GitHub's own summary that required status checks passed and nothing is
   blocking the merge. `UNSTABLE`, `BLOCKED`, `DIRTY`, `BEHIND`, and `UNKNOWN`
   all fail the gate.
5. No check run is in a failing conclusion.
6. Every external review named in `ALFRED_MERGE_REQUIRED_EXTERNAL_REVIEWS` has
   posted a clean verdict on the exact current head. This is empty by default, so
   the gate relies on GitHub's own review settings unless you set it.

The gate fails closed. Any API error, any missing field, or any value it does
not recognise makes it return "not mergeable" rather than guess.

## Where the approval count comes from

The effective approval policy is the stricter of GitHub branch protection and
Alfred's threshold. GitHub's `reviewDecision` enforces protected-branch rules;
Alfred always counts current-head approving reviews and requires at least
`ALFRED_MERGE_MIN_APPROVALS` (default 1). This second check is required because
unprotected repositories can report `APPROVED` after one stale approval.

The count uses the latest decisive review per reviewer. A reviewer who requested
changes and later approved counts once, and only when that approval targets the
PR's exact current head. A comment-only review never overrides an earlier
approval. If any reviewer's latest standing review requests changes, the gate
blocks.

## The merge is SHA-guarded

When the first gate passes, Alfred immediately collects and evaluates a second
complete snapshot before the mutation. A same-head review thread, changed
status, or stale external review therefore blocks the merge. The second head
must also equal the first head. Alfred then squash-merges with that commit
(`gh pr merge --squash --match-head-commit <sha>`), so a push in the remaining
mutation window is rejected by GitHub.

## Policy knobs

| Environment variable | Default | Effect |
| --- | --- | --- |
| `ALFRED_MERGE_REQUIRE_APPROVAL` | on | Require exact-head GitHub approvals. When off with external reviews configured, Alfred still uses every native gate but does not require a separate human approval. |
| `ALFRED_MERGE_MIN_APPROVALS` | 1 | Distinct current-head approvals Alfred always requires. Must be an integer of at least 1; invalid values fail closed. GitHub branch protection may impose a stricter rule. |
| `ALFRED_MERGE_REQUIRED_EXTERNAL_REVIEWS` | empty | Comma-separated external review summaries that must be clean on the exact head. Empty means GitHub's native review settings only. |

## Command line

Run the same predicate by hand against any PR:

```
alfred pr check <number> --repo owner/name
alfred pr merge <number> --repo owner/name
```

`alfred pr check` is read-only. It prints each condition as pass or fail and
exits non-zero when the PR is not mergeable. Add `--json` for machine-readable
output. `alfred pr merge` runs the same check and merges only if every condition
passes, using the SHA-guarded squash. Pass `--no-delete-branch` to keep the head
branch. `--repo` accepts a full `owner/name` slug, or a bare repo name when
`GH_ORG` is set. When `--repo` is omitted, Alfred uses the current checkout's
origin repo name under `GH_ORG`.

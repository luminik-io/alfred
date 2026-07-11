# Merge gate

Alfred merges a pull request only when GitHub itself says it is ready. There is
one predicate, in [`lib/merge_gate.py`](../lib/merge_gate.py), and it reads
GitHub's own machinery. It hard-codes no reviewer names and no review products.
The reviewers are whoever GitHub says approved the PR.

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

When the gate passes, Alfred squash-merges with the head commit captured during
the check (`gh pr merge --squash --match-head-commit <sha>`). If the PR head
moved between the check and the merge, for example a new push landed in that
window, GitHub rejects the merge and the gate fails closed instead of merging
unreviewed changes.

## Policy knobs

| Environment variable | Default | Effect |
| --- | --- | --- |
| `ALFRED_MERGE_REQUIRE_APPROVAL` | on | Require exact-head GitHub approvals. When off with external reviews configured, Alfred still uses every native gate but does not require a separate human approval. |
| `ALFRED_MERGE_MIN_APPROVALS` | 1 | Distinct current-head approvals Alfred always requires. Must be an integer of at least 1; invalid values fail closed. GitHub branch protection may impose a stricter rule. |
| `ALFRED_MERGE_REQUIRED_EXTERNAL_REVIEWS` | empty | Comma-separated external review summaries that must be clean on the exact head. Set `greptile,codex` for Alfred's own repository policy. |

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

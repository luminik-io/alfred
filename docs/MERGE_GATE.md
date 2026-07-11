# Merge gate

Alfred merges a pull request only when GitHub itself says it is ready. There is
one predicate, in [`lib/merge_gate.py`](../lib/merge_gate.py), and it reads
GitHub's own machinery. It hard-codes no reviewer names and no review products.
The reviewers are whoever GitHub says approved the PR.

## The gate

A PR is mergeable by Alfred only when all of these hold:

1. The PR is open.
2. GitHub's `reviewDecision` is `APPROVED`. GitHub aggregates the required
   approval count from branch protection, so on a protected repo this single
   field already encodes your policy: if you require two approving reviews from
   a code-owners team, `reviewDecision` only becomes `APPROVED` once that is
   met.
3. There are zero unresolved review threads, from any author.
4. `mergeStateStatus` is `CLEAN` and `mergeable` is `MERGEABLE`. This is
   GitHub's own summary that required status checks passed and nothing is
   blocking the merge. `UNSTABLE`, `BLOCKED`, `DIRTY`, `BEHIND`, and `UNKNOWN`
   all fail the gate.
5. No check run is in a failing conclusion.

The gate fails closed. Any API error, any missing field, or any value it does
not recognise makes it return "not mergeable" rather than guess.

## Where the approval count comes from

The required number of approvals comes from GitHub branch protection first, not
from Alfred. Set your rule once on the repo (Settings, Branches, branch
protection, "Require a pull request before merging" with the approval count and
any code-owners requirement). GitHub then reports `reviewDecision: APPROVED`
only when that rule is satisfied, and the gate trusts that single field.

On a repo with no branch-protection review rule, `reviewDecision` is null. There
the gate falls back to counting current-head approving reviews itself and
requires at least `ALFRED_MERGE_MIN_APPROVALS` (default 1). The count uses the
latest review per reviewer, so a reviewer who requested changes and later
approved counts as one approval only when that approval targets the PR's current
head. A comment-only review never overrides an earlier approval. If any
reviewer's latest standing review requests changes, the gate blocks.

## The merge is SHA-guarded

When the gate passes, Alfred squash-merges with the head commit captured during
the check (`gh pr merge --squash --match-head-commit <sha>`). If the PR head
moved between the check and the merge, for example a new push landed in that
window, GitHub rejects the merge and the gate fails closed instead of merging
unreviewed changes.

## The two knobs

| Environment variable | Default | Effect |
| --- | --- | --- |
| `ALFRED_MERGE_REQUIRE_APPROVAL` | on | When on, the automerge sweeper only merges PRs that pass this gate. When off, the sweeper keeps its prior review-agent ship-ready behaviour. |
| `ALFRED_MERGE_MIN_APPROVALS` | 1 | Current-head approvals required only on a repo with no branch-protection review rule. Must be an integer of at least 1; invalid values fail closed. Ignored when GitHub already drives `reviewDecision`. |

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

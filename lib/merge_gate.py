"""GitHub-native merge gate.

A single, vendor-neutral predicate that decides whether Alfred may merge a
pull request. It reads GitHub's own machinery and nothing else: the aggregate
review decision, unresolved review threads, the merge state, and check runs.
There are no hard-coded reviewer logins or review-product names anywhere in
this module. The reviewers are whoever GitHub says approved the PR.

A PR is mergeable-by-alfred only when ALL of the following hold:

1. The PR is open.
2. GitHub does not report a blocking review decision, and at least
   ``min_approvals`` distinct approvals target the exact current head. Branch
   protection remains an independent, potentially stricter policy.
3. There are zero unresolved review threads, from any author.
4. ``mergeStateStatus`` is ``CLEAN`` and ``mergeable`` is ``MERGEABLE``. This
   encodes required status checks and the absence of any blocking state.
5. No check run is in a failing conclusion.

The design fails closed: any API error, missing field, or unrecognised value
makes the gate return "not mergeable" rather than guessing.

The evaluation is a pure function over a :class:`GateSnapshot`, so it is fully
unit-testable without touching the network. Snapshot collection and the
SHA-guarded merge are thin wrappers over ``gh`` that accept injectable runners.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

MIN_APPROVALS_DEFAULT = 1

# Check-run conclusions that block a merge. Anything not clearly successful is
# treated as blocking so the gate fails closed.
_FAILING_CONCLUSIONS = frozenset(
    {
        "FAILURE",
        "TIMED_OUT",
        "CANCELLED",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "STALE",
    }
)

# Review states that express a reviewer's standing decision. ``COMMENTED``
# reviews are deliberately excluded: a comment-only review neither approves nor
# blocks, and it must not overwrite an earlier approval or change request.
_DECISIVE_REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


GhJson = Callable[[list[str], Any], Any]
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_gh_json(cmd: list[str], default: Any) -> Any:
    """Run ``gh`` and parse JSON output; return ``default`` on any failure."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return default
    if res.returncode != 0:
        return default
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return default


def _default_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(exc))


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    submitted_at: str
    commit_id: str = ""


@dataclass(frozen=True)
class ReviewThread:
    is_resolved: bool
    path: str
    author: str


@dataclass(frozen=True)
class CheckRun:
    name: str
    conclusion: str


@dataclass(frozen=True)
class GateSnapshot:
    """Everything the gate needs, already fetched from GitHub."""

    state: str
    head_sha: str
    review_decision: str | None
    reviews: tuple[Review, ...]
    review_threads: tuple[ReviewThread, ...]
    merge_state_status: str
    mergeable: str
    checks: tuple[CheckRun, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Condition:
    key: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateDecision:
    mergeable: bool
    head_sha: str
    conditions: list[Condition] = field(default_factory=list)

    def failing(self) -> list[Condition]:
        return [c for c in self.conditions if not c.passed]

    def short_reason(self) -> str:
        """One-line reason a PR is not mergeable (empty when it is)."""
        if self.mergeable:
            return ""
        failing = self.failing()
        if not failing:
            return "not mergeable"
        first = failing[0]
        extra = f" (+{len(failing) - 1} more)" if len(failing) > 1 else ""
        return f"{first.detail}{extra}"

    def render(self) -> str:
        """Multi-line plain-language pass/fail report."""
        lines = []
        for c in self.conditions:
            mark = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{mark}] {c.label}: {c.detail}")
        verdict = "MERGEABLE" if self.mergeable else "NOT MERGEABLE"
        lines.append(f"Gate: {verdict}")
        return "\n".join(lines)


def parse_min_approvals(raw: str | None) -> int:
    """Parse the always-on exact-head approval threshold."""
    value = str(MIN_APPROVALS_DEFAULT) if raw is None else raw.strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("ALFRED_MERGE_MIN_APPROVALS must be an integer >= 1") from exc
    if parsed < 1:
        raise ValueError("ALFRED_MERGE_MIN_APPROVALS must be an integer >= 1")
    return parsed


def _effective_reviews(reviews: Iterable[Review]) -> dict[str, Review]:
    """Return the latest decisive review state per reviewer.

    Reviews are ordered by submission time; the last decisive one per author
    wins, so a reviewer who requested changes and then approved counts as an
    approval. Comment-only reviews are ignored.
    """
    latest: dict[str, Review] = {}
    ordered = sorted(reviews, key=lambda r: r.submitted_at or "")
    for r in ordered:
        state = (r.state or "").upper()
        if state not in _DECISIVE_REVIEW_STATES:
            continue
        author = (r.author or "").lower()
        if not author:
            continue
        latest[author] = r
    return latest


def _approval_condition(snapshot: GateSnapshot, min_approvals: int) -> Condition:
    label = "Approved on GitHub"
    decision = (snapshot.review_decision or "").upper()

    if decision == "CHANGES_REQUESTED":
        return Condition("approved", label, False, "changes requested by a reviewer")
    if decision == "REVIEW_REQUIRED":
        return Condition("approved", label, False, "review required but not yet approved")
    if decision and decision != "APPROVED":
        # Unknown, non-empty decision: fail closed rather than guess.
        return Condition("approved", label, False, f"unrecognised reviewDecision '{decision}'")

    # Always count current-head approvals. On protected repos GitHub's
    # reviewDecision independently enforces the branch rule, while this count
    # enforces Alfred's threshold and exact-head policy. On unprotected repos
    # reviewDecision may become APPROVED after one stale approval, so it is not
    # sufficient by itself.
    effective = _effective_reviews(snapshot.reviews)
    if any((review.state or "").upper() == "CHANGES_REQUESTED" for review in effective.values()):
        return Condition(
            "approved",
            label,
            False,
            "changes requested by a reviewer",
        )
    approvals = sum(
        1
        for review in effective.values()
        if (review.state or "").upper() == "APPROVED"
        # Missing commit_id is unverifiable, even when reviewDecision says
        # APPROVED. Exact-head proof is a hard gate, so fail closed.
        and bool(snapshot.head_sha)
        and review.commit_id == snapshot.head_sha
    )
    if approvals >= min_approvals:
        return Condition(
            "approved",
            label,
            True,
            f"{approvals} current-head approving review(s), need {min_approvals}"
            + ("; GitHub reviewDecision APPROVED" if decision == "APPROVED" else ""),
        )
    return Condition(
        "approved",
        label,
        False,
        f"{approvals} current-head approving review(s), need {min_approvals}"
        + ("; GitHub reviewDecision APPROVED" if decision == "APPROVED" else ""),
    )


def _threads_condition(snapshot: GateSnapshot) -> Condition:
    label = "All review threads resolved"
    unresolved = [t for t in snapshot.review_threads if not t.is_resolved]
    if not unresolved:
        return Condition("threads", label, True, "no unresolved review threads")
    first = unresolved[0]
    where = first.path or "conversation"
    who = first.author or "someone"
    extra = f" (+{len(unresolved) - 1} more)" if len(unresolved) > 1 else ""
    return Condition(
        "threads",
        label,
        False,
        f"{len(unresolved)} unresolved review thread(s): {who} @ {where}{extra}",
    )


def _merge_state_condition(snapshot: GateSnapshot) -> Condition:
    label = "Branch is mergeable and clean"
    merge_state = (snapshot.merge_state_status or "").upper()
    mergeable = (snapshot.mergeable or "").upper()
    if merge_state == "CLEAN" and mergeable == "MERGEABLE":
        return Condition("merge_state", label, True, "mergeStateStatus CLEAN, MERGEABLE")
    detail = f"mergeStateStatus {merge_state or 'UNKNOWN'}, mergeable {mergeable or 'UNKNOWN'}"
    return Condition("merge_state", label, False, detail)


def _checks_condition(snapshot: GateSnapshot) -> Condition:
    label = "No failing checks"
    failing = [c for c in snapshot.checks if (c.conclusion or "").upper() in _FAILING_CONCLUSIONS]
    if not failing:
        return Condition("checks", label, True, "no failing check runs")
    names = ", ".join(sorted({c.name or "check" for c in failing}))
    return Condition("checks", label, False, f"failing check(s): {names}")


def evaluate_gate(
    snapshot: GateSnapshot, *, min_approvals: int = MIN_APPROVALS_DEFAULT
) -> GateDecision:
    """Pure predicate: decide whether the snapshot allows an Alfred merge."""
    # Any collection error is disqualifying: fail closed.
    if snapshot.errors:
        detail = snapshot.errors[0]
        extra = f" (+{len(snapshot.errors) - 1} more)" if len(snapshot.errors) > 1 else ""
        return GateDecision(
            False,
            snapshot.head_sha,
            [Condition("api", "GitHub data collected", False, f"{detail}{extra}")],
        )

    conditions: list[Condition] = []
    state = (snapshot.state or "").upper()
    conditions.append(
        Condition(
            "open",
            "Pull request is open",
            state == "OPEN",
            "open" if state == "OPEN" else f"state is {state or 'UNKNOWN'}",
        )
    )
    conditions.append(_approval_condition(snapshot, min_approvals))
    conditions.append(_threads_condition(snapshot))
    conditions.append(_merge_state_condition(snapshot))
    conditions.append(_checks_condition(snapshot))

    mergeable = all(c.passed for c in conditions)
    return GateDecision(mergeable, snapshot.head_sha, conditions)


def _normalize_check(entry: dict) -> CheckRun | None:
    """Normalise a statusCheckRollup entry into a CheckRun.

    The rollup mixes CheckRun objects (status/conclusion) and legacy
    StatusContext objects (state). Both are mapped onto a single conclusion.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name") or entry.get("context") or "check"
    if "conclusion" in entry or "status" in entry:
        conclusion = (entry.get("conclusion") or "").upper()
        status = (entry.get("status") or "").upper()
        # A check that is not yet complete has no conclusion; leave it blank so
        # it does not count as failing here. Incomplete required checks are
        # already reflected by mergeStateStatus not being CLEAN.
        if not conclusion and status and status != "COMPLETED":
            conclusion = ""
        return CheckRun(str(name), conclusion)
    # StatusContext: map its state onto a conclusion.
    state = (entry.get("state") or "").upper()
    mapping = {
        "SUCCESS": "SUCCESS",
        "FAILURE": "FAILURE",
        "ERROR": "FAILURE",
        "PENDING": "",
        "EXPECTED": "",
    }
    return CheckRun(str(name), mapping.get(state, "FAILURE" if state else ""))


def collect_snapshot(
    repo: str,
    pr_number: int,
    *,
    gh_json: GhJson = _default_gh_json,
) -> GateSnapshot:
    """Fetch everything the gate needs from GitHub for ``owner/repo#number``.

    ``repo`` must be a full ``owner/name`` slug. Fails closed: any missing
    response is recorded as an error on the returned snapshot.
    """
    errors: list[str] = []

    _MISSING = object()
    view = gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "-R",
            repo,
            "--json",
            "state,headRefOid,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup",
        ],
        _MISSING,
    )
    if view is _MISSING or not isinstance(view, dict):
        errors.append("could not read PR from GitHub")
        view = {}

    review_decision = view.get("reviewDecision") if view.get("reviewDecision") else None
    reviews = _collect_reviews(repo, pr_number, gh_json=gh_json, errors=errors)

    checks: list[CheckRun] = []
    for entry in view.get("statusCheckRollup") or []:
        normalized = _normalize_check(entry)
        if normalized is not None:
            checks.append(normalized)

    threads = _collect_review_threads(repo, pr_number, gh_json=gh_json, errors=errors)

    return GateSnapshot(
        state=str(view.get("state") or ""),
        head_sha=str(view.get("headRefOid") or ""),
        review_decision=review_decision,
        reviews=tuple(reviews),
        review_threads=tuple(threads),
        merge_state_status=str(view.get("mergeStateStatus") or ""),
        mergeable=str(view.get("mergeable") or ""),
        checks=tuple(checks),
        errors=tuple(errors),
    )


_REVIEW_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$num:Int!,$endCursor:String){"
    " repository(owner:$owner,name:$name){"
    "  pullRequest(number:$num){"
    "   reviewThreads(first:100,after:$endCursor){"
    "    nodes{isResolved path comments(first:1){nodes{author{login}}}}"
    "    pageInfo{hasNextPage endCursor}"
    "   }"
    "  }"
    " }"
    "}"
)

_REVIEWS_QUERY = (
    "query($owner:String!,$name:String!,$num:Int!,$endCursor:String){"
    " repository(owner:$owner,name:$name){"
    "  pullRequest(number:$num){"
    "   reviews(first:100,after:$endCursor){"
    "    nodes{author{login} state submittedAt commit{oid}}"
    "    pageInfo{hasNextPage endCursor}"
    "   }"
    "  }"
    " }"
    "}"
)


def _collect_reviews(
    repo: str,
    pr_number: int,
    *,
    gh_json: GhJson,
    errors: list[str],
) -> list[Review]:
    """Fetch every review, including the reviewed commit, for fallback gating."""
    owner, _, name = repo.partition("/")
    if not owner or not name:
        errors.append(f"invalid repo slug '{repo}'")
        return []
    reviews: list[Review] = []
    cursor: str | None = None
    while True:
        _MISSING = object()
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_REVIEWS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"num={pr_number}",
            "--jq",
            ".data.repository.pullRequest.reviews",
        ]
        if cursor is not None:
            cmd[cmd.index("--jq") : cmd.index("--jq")] = ["-F", f"endCursor={cursor}"]
        page = gh_json(cmd, _MISSING)
        if page is _MISSING or not isinstance(page, dict):
            errors.append("could not read reviews from GitHub")
            return []
        nodes = page.get("nodes")
        page_info = page.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(page_info, dict):
            errors.append("received malformed review page from GitHub")
            return []
        for item in nodes:
            if not isinstance(item, dict):
                errors.append("received malformed review data from GitHub")
                return []
            author = item.get("author") or {}
            commit = item.get("commit") or {}
            if not isinstance(author, dict) or not isinstance(commit, dict):
                errors.append("received malformed review data from GitHub")
                return []
            reviews.append(
                Review(
                    author=str(author.get("login") or ""),
                    state=str(item.get("state") or ""),
                    submitted_at=str(item.get("submittedAt") or ""),
                    commit_id=str(commit.get("oid") or ""),
                )
            )
        if page_info.get("hasNextPage") is False:
            return reviews
        next_cursor = page_info.get("endCursor")
        if (
            page_info.get("hasNextPage") is not True
            or not isinstance(next_cursor, str)
            or not next_cursor
        ):
            errors.append("review pagination was incomplete")
            return []
        cursor = next_cursor


def _collect_review_threads(
    repo: str,
    pr_number: int,
    *,
    gh_json: GhJson,
    errors: list[str],
) -> list[ReviewThread]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        errors.append(f"invalid repo slug '{repo}'")
        return []
    threads: list[ReviewThread] = []
    cursor: str | None = None
    while True:
        _MISSING = object()
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_REVIEW_THREADS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"num={pr_number}",
            "--jq",
            ".data.repository.pullRequest.reviewThreads",
        ]
        if cursor is not None:
            cmd[cmd.index("--jq") : cmd.index("--jq")] = ["-F", f"endCursor={cursor}"]
        page = gh_json(cmd, _MISSING)
        if page is _MISSING or not isinstance(page, dict):
            errors.append("could not read review threads from GitHub")
            return []
        nodes = page.get("nodes")
        page_info = page.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(page_info, dict):
            errors.append("received malformed review-thread page from GitHub")
            return []
        for node in nodes:
            if not isinstance(node, dict):
                errors.append("received malformed review-thread data from GitHub")
                return []
            comments_connection = node.get("comments") or {}
            if not isinstance(comments_connection, dict):
                errors.append("received malformed review-thread data from GitHub")
                return []
            comments = comments_connection.get("nodes") or []
            if not isinstance(comments, list):
                errors.append("received malformed review-thread data from GitHub")
                return []
            author = ""
            if comments:
                first_comment = comments[0]
                if not isinstance(first_comment, dict):
                    errors.append("received malformed review-thread data from GitHub")
                    return []
                author_data = first_comment.get("author") or {}
                if not isinstance(author_data, dict):
                    errors.append("received malformed review-thread data from GitHub")
                    return []
                author = str(author_data.get("login") or "")
            threads.append(
                ReviewThread(
                    is_resolved=bool(node.get("isResolved")),
                    path=str(node.get("path") or ""),
                    author=str(author),
                )
            )
        if page_info.get("hasNextPage") is False:
            return threads
        next_cursor = page_info.get("endCursor")
        if (
            page_info.get("hasNextPage") is not True
            or not isinstance(next_cursor, str)
            or not next_cursor
        ):
            errors.append("review-thread pagination was incomplete")
            return []
        if next_cursor == cursor:
            errors.append("review-thread pagination cursor did not advance")
            return []
        cursor = next_cursor


def guarded_squash_merge(
    repo: str,
    pr_number: int,
    head_sha: str,
    *,
    delete_branch: bool = True,
    runner: Runner = _default_run,
) -> tuple[bool, str]:
    """Squash-merge, guarded on the head SHA captured during the gate snapshot.

    ``gh pr merge --match-head-commit`` makes GitHub reject the merge if the PR
    head moved between the snapshot and the merge, so a race fails closed
    instead of merging unreviewed changes.
    """
    if not head_sha:
        return False, "refusing to merge without a verified head SHA"
    cmd = [
        "gh",
        "pr",
        "merge",
        str(pr_number),
        "-R",
        repo,
        "--squash",
        "--match-head-commit",
        head_sha,
    ]
    if delete_branch:
        cmd.append("--delete-branch")
    res = runner(cmd)
    if res.returncode == 0:
        return True, "merged"
    return False, (res.stderr or res.stdout or "merge failed").strip()[:300]


def gate_pull_request(
    repo: str,
    pr_number: int,
    *,
    min_approvals: int = MIN_APPROVALS_DEFAULT,
    gh_json: GhJson = _default_gh_json,
) -> tuple[GateSnapshot, GateDecision]:
    """Collect a snapshot and evaluate the gate in one call."""
    snapshot = collect_snapshot(repo, pr_number, gh_json=gh_json)
    decision = evaluate_gate(snapshot, min_approvals=min_approvals)
    return snapshot, decision


def sequence_authors(reviews: Sequence[Review]) -> list[str]:
    """Convenience for callers that want the distinct reviewer logins."""
    seen: list[str] = []
    for r in reviews:
        login = (r.author or "").lower()
        if login and login not in seen:
            seen.append(login)
    return seen

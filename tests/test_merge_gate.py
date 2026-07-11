"""Unit tests for the GitHub-native merge gate predicate and its wrappers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import merge_gate  # noqa: E402
from merge_gate import (  # noqa: E402
    CheckRun,
    GateSnapshot,
    Review,
    ReviewThread,
    collect_snapshot,
    evaluate_gate,
    guarded_squash_merge,
)


def _snapshot(**overrides) -> GateSnapshot:
    """A snapshot that is mergeable by default; override one field per test."""
    base = {
        "state": "OPEN",
        "head_sha": "a" * 40,
        "review_decision": "APPROVED",
        "reviews": (Review("operator", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),),
        "review_threads": (ReviewThread(True, "lib/x.py", "operator"),),
        "merge_state_status": "CLEAN",
        "mergeable": "MERGEABLE",
        "checks": (CheckRun("ci", "SUCCESS"),),
        "errors": (),
    }
    base.update(overrides)
    return GateSnapshot(**base)


# --------------------------------------------------------------------------
# evaluate_gate: the pure predicate
# --------------------------------------------------------------------------


def test_approved_and_clean_is_mergeable():
    decision = evaluate_gate(_snapshot())
    assert decision.mergeable is True
    assert decision.head_sha == "a" * 40
    assert decision.failing() == []


def test_github_approved_still_requires_configured_current_head_approvals():
    stale = _snapshot(
        review_decision="APPROVED",
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", "b" * 40),),
    )
    assert evaluate_gate(stale, min_approvals=1).mergeable is False

    missing_commit = _snapshot(
        review_decision="APPROVED",
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", ""),),
    )
    assert evaluate_gate(missing_commit, min_approvals=1).mergeable is False

    two_current = _snapshot(
        review_decision="APPROVED",
        reviews=(
            Review("alice", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),
            Review("bob", "APPROVED", "2026-07-11T10:01:00Z", "a" * 40),
        ),
    )
    assert evaluate_gate(two_current, min_approvals=2).mergeable is True


def test_unresolved_thread_blocks_merge():
    snap = _snapshot(
        review_threads=(
            ReviewThread(True, "a.py", "operator"),
            ReviewThread(False, "b.py", "someone-else"),
        )
    )
    decision = evaluate_gate(snap)
    assert decision.mergeable is False
    keys = {c.key for c in decision.failing()}
    assert keys == {"threads"}
    assert "someone-else" in decision.short_reason()


def test_changes_requested_blocks_merge():
    decision = evaluate_gate(_snapshot(review_decision="CHANGES_REQUESTED"))
    assert decision.mergeable is False
    assert {c.key for c in decision.failing()} == {"approved"}


def test_review_required_blocks_merge():
    decision = evaluate_gate(_snapshot(review_decision="REVIEW_REQUIRED"))
    assert decision.mergeable is False
    assert {c.key for c in decision.failing()} == {"approved"}


def test_approved_but_unstable_merge_state_blocks_merge():
    # mergeStateStatus UNSTABLE means a non-required check is failing or pending.
    decision = evaluate_gate(_snapshot(merge_state_status="UNSTABLE"))
    assert decision.mergeable is False
    assert {c.key for c in decision.failing()} == {"merge_state"}


def test_conflicting_mergeable_blocks_merge():
    decision = evaluate_gate(_snapshot(mergeable="CONFLICTING", merge_state_status="DIRTY"))
    assert decision.mergeable is False
    assert "merge_state" in {c.key for c in decision.failing()}


def test_failing_check_blocks_merge():
    snap = _snapshot(
        merge_state_status="CLEAN",
        checks=(CheckRun("ci", "SUCCESS"), CheckRun("lint", "FAILURE")),
    )
    decision = evaluate_gate(snap)
    assert decision.mergeable is False
    failing = decision.failing()
    assert {c.key for c in failing} == {"checks"}
    assert "lint" in failing[0].detail


def test_closed_pr_blocks_merge():
    decision = evaluate_gate(_snapshot(state="MERGED"))
    assert decision.mergeable is False
    assert "open" in {c.key for c in decision.failing()}


def test_api_error_fails_closed():
    snap = _snapshot(errors=("could not read PR from GitHub",))
    decision = evaluate_gate(snap)
    assert decision.mergeable is False
    assert decision.failing()[0].key == "api"
    # Fail-closed short-circuits: no partial condition list is trusted.
    assert len(decision.conditions) == 1


def test_unknown_review_decision_fails_closed():
    decision = evaluate_gate(_snapshot(review_decision="SOMETHING_NEW"))
    assert decision.mergeable is False
    assert {c.key for c in decision.failing()} == {"approved"}


# --------------------------------------------------------------------------
# Min-approvals fallback: repos without branch protection
# --------------------------------------------------------------------------


def test_no_branch_protection_counts_approvals():
    # reviewDecision is None -> count approving reviews against min_approvals.
    snap = _snapshot(
        review_decision=None,
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),),
    )
    assert evaluate_gate(snap, min_approvals=1).mergeable is True
    assert evaluate_gate(snap, min_approvals=2).mergeable is False


def test_no_branch_protection_re_review_latest_wins():
    # A reviewer requested changes and then approved: latest wins -> 1 approval.
    snap = _snapshot(
        review_decision=None,
        reviews=(
            Review("operator", "CHANGES_REQUESTED", "2026-07-11T09:00:00Z", "a" * 40),
            Review("operator", "APPROVED", "2026-07-11T11:00:00Z", "a" * 40),
        ),
    )
    assert evaluate_gate(snap, min_approvals=1).mergeable is True


def test_no_branch_protection_comment_does_not_override_approval():
    # A later comment-only review must not downgrade an approval.
    snap = _snapshot(
        review_decision=None,
        reviews=(
            Review("operator", "APPROVED", "2026-07-11T09:00:00Z", "a" * 40),
            Review("operator", "COMMENTED", "2026-07-11T12:00:00Z"),
        ),
    )
    assert evaluate_gate(snap, min_approvals=1).mergeable is True


def test_no_branch_protection_latest_change_request_blocks():
    snap = _snapshot(
        review_decision=None,
        reviews=(
            Review("operator", "APPROVED", "2026-07-11T09:00:00Z"),
            Review("operator", "CHANGES_REQUESTED", "2026-07-11T12:00:00Z"),
        ),
    )
    decision = evaluate_gate(snap, min_approvals=1)
    assert decision.mergeable is False
    assert {c.key for c in decision.failing()} == {"approved"}


def test_no_branch_protection_two_distinct_approvers():
    snap = _snapshot(
        review_decision=None,
        reviews=(
            Review("alice", "APPROVED", "2026-07-11T09:00:00Z", "a" * 40),
            Review("bob", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),
        ),
    )
    assert evaluate_gate(snap, min_approvals=2).mergeable is True
    # Same person approving twice is still one approver.
    snap_one = _snapshot(
        review_decision=None,
        reviews=(
            Review("alice", "APPROVED", "2026-07-11T09:00:00Z", "a" * 40),
            Review("alice", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),
        ),
    )
    assert evaluate_gate(snap_one, min_approvals=2).mergeable is False


def test_no_branch_protection_rejects_approval_for_stale_head():
    snap = _snapshot(
        review_decision=None,
        reviews=(Review("alice", "APPROVED", "2026-07-11T09:00:00Z", "b" * 40),),
    )
    decision = evaluate_gate(snap, min_approvals=1)
    assert decision.mergeable is False
    assert "0 current-head approving review" in decision.short_reason()


# --------------------------------------------------------------------------
# collect_snapshot: fetch + normalise from gh
# --------------------------------------------------------------------------


def _fake_gh_json(view_payload, threads_payload, reviews_payload=None):
    review_page = 0

    def _runner(cmd, default):
        nonlocal review_page
        if any("reviews(first:100" in str(arg) for arg in cmd):
            pages = reviews_payload if reviews_payload is not None else [[]]
            if review_page >= len(pages):
                return default
            raw_nodes = pages[review_page]
            nodes = [
                {
                    "author": item.get("user") or {},
                    "state": item.get("state"),
                    "submittedAt": item.get("submitted_at"),
                    "commit": {"oid": item.get("commit_id")},
                }
                for item in raw_nodes
            ]
            review_page += 1
            return {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": review_page < len(pages),
                    "endCursor": f"review-cursor-{review_page}",
                },
            }
        if "graphql" in cmd:
            if threads_payload is None:
                return default
            return {
                "nodes": threads_payload,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        return view_payload if view_payload is not None else default

    return _runner


def test_collect_snapshot_builds_from_gh():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviews": [
            {
                "author": {"login": "operator"},
                "state": "APPROVED",
                "submittedAt": "2026-07-11T10:00:00Z",
            }
        ],
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "ci",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"},
        ],
    }
    threads = [
        {
            "isResolved": True,
            "path": "a.py",
            "comments": {"nodes": [{"author": {"login": "operator"}}]},
        }
    ]
    review_pages = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(view, threads, review_pages))
    assert snap.errors == ()
    assert snap.state == "OPEN"
    assert snap.head_sha == "b" * 40
    assert snap.review_decision == "APPROVED"
    assert len(snap.reviews) == 1
    assert len(snap.checks) == 2
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_missing_view_fails_closed():
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(None, []))
    assert snap.errors
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_missing_threads_fails_closed():
    view = {"state": "OPEN", "headRefOid": "c" * 40, "reviewDecision": "APPROVED"}
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(view, None))
    assert any("review threads" in e for e in snap.errors)
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_null_review_decision_becomes_none():
    view = {
        "state": "OPEN",
        "headRefOid": "d" * 40,
        "reviewDecision": "",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    review_pages = [
        [
            {
                "user": {"login": "alice"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "d" * 40,
            }
        ]
    ]
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(view, [], review_pages))
    assert snap.review_decision is None
    assert evaluate_gate(snap, min_approvals=1).mergeable is True


def test_collect_snapshot_reads_current_head_approval_from_later_review_page():
    view = {
        "state": "OPEN",
        "headRefOid": "d" * 40,
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    review_pages = [
        [
            {
                "user": {"login": "alice"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T09:00:00Z",
                "commit_id": "c" * 40,
            }
        ],
        [
            {
                "user": {"login": "alice"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "d" * 40,
            }
        ],
    ]
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(view, [], review_pages))
    assert len(snap.reviews) == 2
    assert evaluate_gate(snap, min_approvals=1).mergeable is True


def test_collect_snapshot_paginates_all_review_threads():
    view = {
        "state": "OPEN",
        "headRefOid": "e" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    calls = []

    def _runner(cmd, default):
        if any("reviews(first:100" in str(arg) for arg in cmd):
            return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        if "graphql" not in cmd:
            return view
        calls.append(cmd)
        if any(arg == "endCursor=cursor-1" for arg in cmd):
            return {
                "nodes": [
                    {
                        "isResolved": False,
                        "path": "late.py",
                        "comments": {"nodes": [{"author": {"login": "reviewer"}}]},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"},
            }
        return {
            "nodes": [{"isResolved": True, "path": "early.py", "comments": {"nodes": []}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
        }

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)
    assert snap.errors == ()
    assert len(calls) == 2
    assert len(snap.review_threads) == 2
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_fails_closed_on_incomplete_thread_pagination():
    view = {
        "state": "OPEN",
        "headRefOid": "e" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }

    def _runner(cmd, default):
        if any("reviews(first:100" in str(arg) for arg in cmd):
            return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        if "graphql" not in cmd:
            return view
        return {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)
    assert any("pagination" in error for error in snap.errors)
    assert evaluate_gate(snap).mergeable is False


def test_parse_min_approvals_rejects_invalid_values():
    assert merge_gate.parse_min_approvals(None) == 1
    assert merge_gate.parse_min_approvals("3") == 3
    for raw in ("", "0", "-2", "two"):
        with pytest.raises(ValueError, match="integer >= 1"):
            merge_gate.parse_min_approvals(raw)


def test_collect_snapshot_pending_check_is_not_failing():
    view = {
        "state": "OPEN",
        "headRefOid": "e" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviews": [],
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "slow", "status": "IN_PROGRESS", "conclusion": ""}
        ],
    }
    review_pages = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "e" * 40,
            }
        ]
    ]
    snap = collect_snapshot("acme/repo", 7, gh_json=_fake_gh_json(view, [], review_pages))
    # A pending check has no failing conclusion; the merge_state field is what
    # would actually gate an incomplete required check.
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_bad_repo_slug_fails_closed():
    snap = collect_snapshot("no-slash", 7, gh_json=_fake_gh_json({"state": "OPEN"}, []))
    assert any("invalid repo slug" in e for e in snap.errors)
    assert evaluate_gate(snap).mergeable is False


# --------------------------------------------------------------------------
# guarded_squash_merge: SHA-guarded merge
# --------------------------------------------------------------------------


def test_guarded_merge_uses_match_head_commit():
    captured = {}

    def _runner(cmd):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="merged", stderr="")

    ok, _msg = guarded_squash_merge("acme/repo", 7, "f" * 40, runner=_runner)
    assert ok is True
    cmd = captured["cmd"]
    assert "--squash" in cmd
    assert "--match-head-commit" in cmd
    assert cmd[cmd.index("--match-head-commit") + 1] == "f" * 40
    assert "--delete-branch" in cmd


def test_guarded_merge_refuses_without_head_sha():
    called = {"n": 0}

    def _runner(cmd):
        called["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ok, msg = guarded_squash_merge("acme/repo", 7, "", runner=_runner)
    assert ok is False
    assert called["n"] == 0
    assert "head SHA" in msg


def test_guarded_merge_reports_failure():
    def _runner(cmd):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="head branch was modified")

    ok, msg = guarded_squash_merge("acme/repo", 7, "a" * 40, runner=_runner)
    assert ok is False
    assert "head branch was modified" in msg


def test_gate_pull_request_end_to_end():
    view = {
        "state": "OPEN",
        "headRefOid": "a" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviews": [],
        "statusCheckRollup": [],
    }
    review_pages = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "a" * 40,
            }
        ]
    ]
    snap, decision = merge_gate.gate_pull_request(
        "acme/repo", 7, gh_json=_fake_gh_json(view, [], review_pages)
    )
    assert decision.mergeable is True
    assert snap.head_sha == "a" * 40

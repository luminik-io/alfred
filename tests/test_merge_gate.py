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
    ExternalReviewEvidence,
    GateSnapshot,
    RequiredCheck,
    Review,
    ReviewThread,
    collect_snapshot,
    evaluate_gate,
    rechecked_squash_merge,
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
        "native_thread_resolution": True,
        "required_checks": (RequiredCheck("Greptile Review", 867647),),
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


def test_required_external_reviews_must_be_clean_and_exact_head():
    head = "a" * 40
    snap = _snapshot(
        external_reviews=(
            ExternalReviewEvidence(
                "greptile-apps[bot]",
                f"Confidence Score: 5/5\nNo blocking issues\nLast reviewed commit: x/commit/{head}",
                "2026-07-11T10:00:00Z",
                head,
            ),
            ExternalReviewEvidence(
                "chatgpt-codex-connector[bot]",
                "Codex Review: Didn't find any major issues.\n\n**Reviewed commit:** `aaaaaaaaaa`",
                "2026-07-11T10:01:00Z",
                head,
            ),
        )
    )
    assert evaluate_gate(snap, required_external_reviews=("greptile", "codex")).mergeable

    stale = _snapshot(external_reviews=snap.external_reviews[:-1])
    decision = evaluate_gate(stale, required_external_reviews=("greptile", "codex"))
    assert decision.mergeable is False
    assert "codex" in decision.short_reason()


def test_required_external_reviews_reject_spoofed_bot_login():
    snap = _snapshot(
        external_reviews=(
            ExternalReviewEvidence(
                "not-greptile-apps[bot]",
                f"Confidence Score: 5/5\nNo blocking issues\nLast reviewed commit: x/commit/{'a' * 40}",
                "2026-07-11T10:00:00Z",
                "a" * 40,
            ),
        )
    )
    assert not evaluate_gate(snap, required_external_reviews=("greptile",)).mergeable


def test_required_external_reviews_require_native_provider_guards():
    head = "a" * 40
    greptile = ExternalReviewEvidence(
        "greptile-apps[bot]",
        "Confidence Score: 5/5\nNo blocking issues",
        "2026-07-11T10:00:00Z",
        head,
    )
    codex = ExternalReviewEvidence(
        "chatgpt-codex-connector[bot]",
        "Codex Review: Didn't find any major issues.",
        "2026-07-11T10:01:00Z",
        head,
    )

    no_check = _snapshot(external_reviews=(greptile,), required_checks=())
    assert not evaluate_gate(no_check, required_external_reviews=("greptile",)).mergeable

    unpinned_check = _snapshot(
        external_reviews=(greptile,),
        required_checks=(RequiredCheck("Greptile Review"),),
    )
    assert not evaluate_gate(unpinned_check, required_external_reviews=("greptile",)).mergeable

    wrong_app = _snapshot(
        external_reviews=(greptile,),
        required_checks=(RequiredCheck("Greptile Review", 1234),),
    )
    assert not evaluate_gate(wrong_app, required_external_reviews=("greptile",)).mergeable

    no_threads = _snapshot(
        external_reviews=(codex,),
        native_thread_resolution=False,
    )
    assert not evaluate_gate(no_threads, required_external_reviews=("codex",)).mergeable

    unknown = _snapshot(external_reviews=(greptile,))
    decision = evaluate_gate(unknown, required_external_reviews=("other-reviewer",))
    assert not decision.mergeable
    assert "missing native merge guard" in decision.short_reason()


def test_codex_resolved_commit_must_equal_head_after_force_push():
    evidence = ExternalReviewEvidence(
        "chatgpt-codex-connector[bot]",
        "Didn't find any major issues.\n**Reviewed commit:** `aaaaaaa`",
        "2026-07-11T10:00:00Z",
        "aaaaaaa" + "b" * 33,
    )
    snap = _snapshot(external_reviews=(evidence,))
    assert not evaluate_gate(snap, required_external_reviews=("codex",)).mergeable


def test_greptile_uses_explicit_last_reviewed_commit_not_incidental_sha():
    body = (
        f"Confidence Score: 5/5\nNo blocking issues\nCurrent {'a' * 40}\n"
        f"Last reviewed commit: x/commit/{'b' * 40}"
    )
    snap = _snapshot(
        external_reviews=(
            ExternalReviewEvidence("greptile-apps[bot]", body, "2026-07-11", "b" * 40),
        )
    )
    assert not evaluate_gate(snap, required_external_reviews=("greptile",)).mergeable


def test_codex_evidence_binds_to_trusted_full_head_request():
    old_head = "aaaaaaa" + "b" * 33
    new_head = "aaaaaaa" + "c" * 33
    payload = [
        [
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{old_head}`",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:00:00Z",
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "Didn't find any major issues.\n**Reviewed commit:** `aaaaaaa`",
                "created_at": "2026-07-11T10:01:00Z",
            },
        ]
    ]
    errors: list[str] = []
    evidence = merge_gate._collect_external_reviews(
        "acme/repo", 7, gh_json=lambda _cmd, _default: payload, errors=errors
    )
    assert errors == []
    assert evidence[-1].reviewed_sha == old_head
    snap = _snapshot(head_sha=new_head, external_reviews=tuple(evidence))
    assert not evaluate_gate(snap, required_external_reviews=("codex",)).mergeable


def test_codex_evidence_uses_comment_order_when_timestamps_tie():
    old_head = "a" * 40
    new_head = "b" * 40
    timestamp = "2026-07-11T10:00:00Z"
    payload = [
        [
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{old_head}`",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "Didn't find any major issues.\nReviewed commit: aaaaaaaaaa",
                "created_at": timestamp,
            },
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{new_head}`",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        ]
    ]
    evidence = merge_gate._collect_external_reviews(
        "acme/repo", 7, gh_json=lambda _cmd, _default: payload, errors=[]
    )
    assert evidence[1].reviewed_sha == old_head


def test_codex_evidence_accepts_unquoted_exact_head_request():
    head = "a" * 40
    payload = [
        [
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: {head}",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:00:00Z",
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "Didn't find any major issues.\nReviewed commit: aaaaaaaaaa",
            },
        ]
    ]
    evidence = merge_gate._collect_external_reviews(
        "acme/repo", 7, gh_json=lambda _cmd, _default: payload, errors=[]
    )
    assert evidence[-1].reviewed_sha == head


def test_codex_evidence_rejects_delayed_response_with_shared_prefix():
    prefix = "abcdef0"
    old_head = prefix + "a" * 33
    new_head = prefix + "b" * 33
    payload = [
        [
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{old_head}`",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:00:00Z",
            },
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{new_head}`",
                "created_at": "2026-07-11T10:01:00Z",
                "updated_at": "2026-07-11T10:01:00Z",
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": f"Didn't find any major issues.\nReviewed commit: {prefix}",
            },
        ]
    ]
    evidence = merge_gate._collect_external_reviews(
        "acme/repo", 7, gh_json=lambda _cmd, _default: payload, errors=[]
    )
    assert evidence[-1].reviewed_sha == ""
    snap = _snapshot(head_sha=new_head, external_reviews=tuple(evidence))
    assert not evaluate_gate(snap, required_external_reviews=("codex",)).mergeable


def test_codex_evidence_rejects_edited_exact_head_request():
    head = "a" * 40
    payload = [
        [
            {
                "user": {"login": "operator"},
                "author_association": "OWNER",
                "body": f"@codex review\nExact head: `{head}`",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:02:00Z",
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "Didn't find any major issues.\nReviewed commit: aaaaaaaaaa",
                "created_at": "2026-07-11T10:01:00Z",
                "updated_at": "2026-07-11T10:01:00Z",
            },
        ]
    ]
    evidence = merge_gate._collect_external_reviews(
        "acme/repo", 7, gh_json=lambda _cmd, _default: payload, errors=[]
    )
    assert evidence[-1].reviewed_sha == ""
    snap = _snapshot(head_sha=head, external_reviews=tuple(evidence))
    assert not evaluate_gate(snap, required_external_reviews=("codex",)).mergeable


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


def _fake_gh_json(
    view_payload,
    threads_payload,
    reviews_payload=None,
    *,
    native_thread_resolution=True,
):
    review_page = 0

    def _runner(cmd, default):
        nonlocal review_page
        if any("/rulesets/" in str(arg) for arg in cmd):
            return {
                "enforcement": "active",
                "current_user_can_bypass": "never",
            }
        if any("/rules/branches/" in str(arg) for arg in cmd):
            return [
                {
                    "type": "pull_request",
                    "ruleset_id": 42,
                    "parameters": {
                        "required_review_thread_resolution": native_thread_resolution,
                    },
                },
                {
                    "type": "required_status_checks",
                    "ruleset_id": 42,
                    "parameters": {
                        "required_status_checks": [
                            {"context": "Greptile Review", "integration_id": 867647},
                        ],
                    },
                },
            ]
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
        if view_payload is None:
            return default
        return {"baseRefName": "main", **view_payload}

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
    assert snap.native_thread_resolution is True
    assert snap.required_checks == (RequiredCheck("Greptile Review", 867647),)
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_keeps_latest_run_for_duplicate_check_name():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "pytest",
                "checkSuite": {"workflowRun": {"workflow": {"name": "CI"}}},
                "status": "COMPLETED",
                "conclusion": "CANCELLED",
                "startedAt": "2026-07-15T05:00:00Z",
            },
            {
                "__typename": "CheckRun",
                "name": "pytest",
                "checkSuite": {"workflowRun": {"workflow": {"name": "CI"}}},
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-07-15T05:05:00Z",
            },
        ],
    }
    reviews = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]

    snap = collect_snapshot(
        "acme/repo",
        7,
        gh_json=_fake_gh_json(view, [], reviews),
    )

    assert snap.checks == (CheckRun("pytest", "SUCCESS"),)
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_preserves_same_named_checks_from_distinct_workflows():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "verify",
                "workflowName": "Backend CI",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "startedAt": "2026-07-15T05:00:00Z",
            },
            {
                "__typename": "CheckRun",
                "name": "verify",
                "workflowName": "Frontend CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-07-15T05:05:00Z",
            },
        ],
    }
    reviews = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]

    snap = collect_snapshot(
        "acme/repo",
        7,
        gh_json=_fake_gh_json(view, [], reviews),
    )

    assert snap.checks == (
        CheckRun("verify", "FAILURE"),
        CheckRun("verify", "SUCCESS"),
    )
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_requires_native_thread_resolution():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    reviews = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]
    snap = collect_snapshot(
        "acme/repo",
        7,
        gh_json=_fake_gh_json(
            view,
            [],
            reviews,
            native_thread_resolution=False,
        ),
    )

    decision = evaluate_gate(snap)

    assert decision.mergeable is False
    assert "not required by the base branch rules" in decision.short_reason()


def test_collect_snapshot_reads_all_effective_rule_pages():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    reviews = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]
    base_runner = _fake_gh_json(view, [], reviews)

    def _runner(cmd, default):
        if any("/rules/branches/" in str(arg) for arg in cmd):
            return [
                [{"type": "deletion", "ruleset_id": 41}],
                [
                    {
                        "type": "pull_request",
                        "ruleset_id": 42,
                        "parameters": {
                            "required_review_thread_resolution": True,
                        },
                    }
                ],
            ]
        return base_runner(cmd, default)

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert snap.native_thread_resolution is True
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_accepts_non_bypassable_classic_protection():
    view = {
        "state": "OPEN",
        "headRefOid": "b" * 40,
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    reviews = [
        [
            {
                "user": {"login": "operator"},
                "state": "APPROVED",
                "submitted_at": "2026-07-11T10:00:00Z",
                "commit_id": "b" * 40,
            }
        ]
    ]
    base_runner = _fake_gh_json(
        view,
        [],
        reviews,
        native_thread_resolution=False,
    )

    def _runner(cmd, default):
        if any("/branches/main/protection" in str(arg) for arg in cmd):
            return {
                "required_conversation_resolution": {"enabled": True},
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {},
                "required_status_checks": {
                    "checks": [{"context": "Greptile Review", "app_id": 867647}],
                },
            }
        return base_runner(cmd, default)

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert snap.native_thread_resolution is True
    assert snap.required_checks == (RequiredCheck("Greptile Review", 867647),)
    assert evaluate_gate(snap).mergeable is True


def test_collect_snapshot_rejects_ruleset_current_user_can_bypass():
    base_runner = _fake_gh_json(
        {
            "state": "OPEN",
            "headRefOid": "b" * 40,
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        },
        [],
        [
            [
                {
                    "user": {"login": "operator"},
                    "state": "APPROVED",
                    "submitted_at": "2026-07-11T10:00:00Z",
                    "commit_id": "b" * 40,
                }
            ]
        ],
    )

    def _runner(cmd, default):
        if any("/rulesets/" in str(arg) for arg in cmd):
            return {
                "enforcement": "active",
                "current_user_can_bypass": "always",
            }
        return base_runner(cmd, default)

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert snap.native_thread_resolution is False
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_rejects_bypassable_classic_protection():
    base_runner = _fake_gh_json(
        {
            "state": "OPEN",
            "headRefOid": "b" * 40,
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        },
        [],
        [
            [
                {
                    "user": {"login": "operator"},
                    "state": "APPROVED",
                    "submitted_at": "2026-07-11T10:00:00Z",
                    "commit_id": "b" * 40,
                }
            ]
        ],
        native_thread_resolution=False,
    )

    def _runner(cmd, default):
        if any("/branches/main/protection" in str(arg) for arg in cmd):
            return {
                "required_conversation_resolution": {"enabled": True},
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {
                    "bypass_pull_request_allowances": {
                        "users": [{"login": "operator"}],
                        "teams": [],
                        "apps": [],
                    }
                },
            }
        return base_runner(cmd, default)

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert snap.native_thread_resolution is False
    assert evaluate_gate(snap).mergeable is False


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


def test_collect_snapshot_fails_closed_on_nonadvancing_review_cursor():
    view = {
        "state": "OPEN",
        "headRefOid": "d" * 40,
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    calls = 0

    def _runner(cmd, default):
        nonlocal calls
        if any("reviews(first:100" in str(arg) for arg in cmd):
            calls += 1
            return {
                "nodes": [],
                "pageInfo": {"hasNextPage": True, "endCursor": "stuck"},
            }
        if "graphql" in cmd:
            return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return view

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert calls == 2
    assert any("review pagination" in error for error in snap.errors)
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_fails_closed_on_review_cursor_cycle():
    view = {
        "state": "OPEN",
        "headRefOid": "d" * 40,
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    cursors = iter(("cursor-a", "cursor-b", "cursor-a"))
    calls = 0

    def _runner(cmd, default):
        nonlocal calls
        if any("reviews(first:100" in str(arg) for arg in cmd):
            calls += 1
            return {
                "nodes": [],
                "pageInfo": {"hasNextPage": True, "endCursor": next(cursors)},
            }
        if "graphql" in cmd:
            return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return view

    snap = collect_snapshot("acme/repo", 7, gh_json=_runner)

    assert calls == 3
    assert any("review pagination" in error for error in snap.errors)
    assert evaluate_gate(snap).mergeable is False


def test_collect_snapshot_paginates_all_review_threads():
    view = {
        "state": "OPEN",
        "headRefOid": "e" * 40,
        "baseRefName": "main",
        "reviewDecision": "APPROVED",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    calls = []

    def _runner(cmd, default):
        if any("/rules/branches/" in str(arg) for arg in cmd):
            return [
                {
                    "type": "pull_request",
                    "ruleset_id": 42,
                    "parameters": {"required_review_thread_resolution": True},
                }
            ]
        if any("/rulesets/42" in str(arg) for arg in cmd):
            return {
                "enforcement": "active",
                "current_user_can_bypass": "never",
            }
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


def test_parse_require_approval_defaults_on_and_rejects_invalid_values():
    assert merge_gate.parse_require_approval(None) is True
    assert merge_gate.parse_require_approval("") is True
    assert merge_gate.parse_require_approval(" true ") is True
    assert merge_gate.parse_require_approval("0") is False
    assert merge_gate.parse_require_approval("off") is False
    with pytest.raises(ValueError, match="must be true or false"):
        merge_gate.parse_require_approval("sometimes")


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
# rechecked_squash_merge: fresh gate plus SHA-guarded merge
# --------------------------------------------------------------------------


def test_rechecked_merge_uses_match_head_commit(monkeypatch):
    captured = {}
    head = "f" * 40
    snap = _snapshot(
        head_sha=head,
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", head),),
    )
    monkeypatch.setattr(
        merge_gate,
        "gate_pull_request",
        lambda *args, **kwargs: (snap, evaluate_gate(snap)),
    )

    def _runner(cmd):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="merged", stderr="")

    ok, _msg = rechecked_squash_merge("acme/repo", 7, head, runner=_runner)
    assert ok is True
    cmd = captured["cmd"]
    assert "--squash" in cmd
    assert "--match-head-commit" in cmd
    assert cmd[cmd.index("--match-head-commit") + 1] == head
    assert "--delete-branch" in cmd


def test_rechecked_merge_refuses_without_head_sha():
    called = {"n": 0}

    def _runner(cmd):
        called["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ok, msg = rechecked_squash_merge("acme/repo", 7, "", runner=_runner)
    assert ok is False
    assert called["n"] == 0
    assert "head SHA" in msg


def test_rechecked_merge_reports_mutation_failure(monkeypatch):
    snap = _snapshot()
    monkeypatch.setattr(
        merge_gate,
        "gate_pull_request",
        lambda *args, **kwargs: (snap, evaluate_gate(snap)),
    )

    def _runner(cmd):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="head branch was modified")

    ok, msg = rechecked_squash_merge("acme/repo", 7, "a" * 40, runner=_runner)
    assert ok is False
    assert "head branch was modified" in msg


def test_rechecked_merge_blocks_late_review_thread(monkeypatch):
    snap = _snapshot(review_threads=(ReviewThread(False, "x.py", "codex"),))
    monkeypatch.setattr(
        merge_gate,
        "gate_pull_request",
        lambda *args, **kwargs: (snap, evaluate_gate(snap)),
    )
    called = {"n": 0}

    def _runner(cmd):
        called["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="merged", stderr="")

    ok, msg = rechecked_squash_merge("acme/repo", 7, "a" * 40, runner=_runner)

    assert ok is False
    assert called["n"] == 0
    assert "unresolved review thread" in msg


def test_rechecked_merge_blocks_head_change_even_when_new_head_is_clean(monkeypatch):
    new_head = "b" * 40
    snap = _snapshot(
        head_sha=new_head,
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", new_head),),
    )
    monkeypatch.setattr(
        merge_gate,
        "gate_pull_request",
        lambda *args, **kwargs: (snap, evaluate_gate(snap)),
    )
    called = {"n": 0}

    def _runner(cmd):
        called["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="merged", stderr="")

    ok, msg = rechecked_squash_merge("acme/repo", 7, "a" * 40, runner=_runner)

    assert ok is False
    assert called["n"] == 0
    assert "new head" in msg


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

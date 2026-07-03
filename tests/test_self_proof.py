#!/usr/bin/env python3
"""Tests for lib/self_proof.py: the quotable self-proof stat.

The stat is "X% of merged PRs were shipped by Alfred agents" per repo and in
aggregate. All GitHub access is stubbed via an injected ``gh_json`` callable,
so these run offline and deterministically. The numerator is agent-shipped
merged PRs (provenance label or agent branch prefix); the denominator is every
merged PR in the window, so the percentage is the fleet's real share of the
merge stream.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "lib"))

import self_proof as sp  # noqa: E402

# A fixed "now" so the cutoff math is deterministic. The window default is 7d.
NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)


def _iso(day: int) -> str:
    return datetime(2026, 6, day, 12, 0, 0, tzinfo=UTC).isoformat()


def _pr(number, *, merged_day, labels=None, branch="feature/x", author="alice"):
    return {
        "number": number,
        "title": f"pr {number}",
        "url": f"https://github.com/acme/api/pull/{number}",
        "author": {"login": author},
        "mergedAt": _iso(merged_day),
        "labels": [{"name": name} for name in (labels or [])],
        "headRefName": branch,
    }


def _gh_for(repo_prs: dict[str, list[dict]]):
    """Build a gh_json stub returning per-repo merged PR rows.

    ``repo_prs`` maps ``owner/name`` -> list of PR rows. A repo mapped to
    ``None`` simulates a gh failure (returns ``None``).
    """

    def _impl(args, **kwargs):
        # args look like ["pr", "list", "--repo", "<repo>", ...]
        if args[0] != "pr":
            return None
        try:
            repo = args[args.index("--repo") + 1]
        except (ValueError, IndexError):
            return None
        return repo_prs.get(repo)

    return _impl


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip self-proof env knobs so tests exercise the coded defaults."""
    for name in (
        "ALFRED_SELF_PROOF_REPOS",
        "ALFRED_SELF_PROOF_SELF_REPO",
        "ALFRED_SELF_REPO",
        "ALFRED_SHIPPED_REPOS",
        "ALFRED_SHIPPED_AGENT_LABELS",
        "ALFRED_SHIPPED_AGENT_BRANCH_PREFIXES",
        "ALFRED_SELF_PROOF_EXCLUDED_AUTHORS",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# agent-shipped detection
# --------------------------------------------------------------------------


def test_agent_shipped_by_provenance_label():
    assert sp.pr_is_agent_shipped(_pr(1, merged_day=28, labels=["agent:authored"]))
    assert sp.pr_is_agent_shipped(_pr(2, merged_day=28, labels=["agent:shipped"]))


def test_agent_shipped_by_branch_prefix():
    assert sp.pr_is_agent_shipped(_pr(3, merged_day=28, branch="lucius/fix-bug"))
    assert sp.pr_is_agent_shipped(_pr(4, merged_day=28, branch="batman/rollout"))


def test_human_pr_is_not_agent_shipped():
    assert not sp.pr_is_agent_shipped(
        _pr(5, merged_day=28, branch="feature/manual", labels=["bug"])
    )


def test_dependabot_on_automerge_branch_is_excluded():
    # An excluded author on an agent-looking branch is NOT counted, so a bot
    # bump never inflates the numerator.
    assert not sp.pr_is_agent_shipped(
        _pr(6, merged_day=28, branch="automerge/dep", author="dependabot[bot]")
    )


# --------------------------------------------------------------------------
# per-repo + aggregate computation
# --------------------------------------------------------------------------


def test_share_and_aggregate_across_repos():
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, labels=["agent:authored"]),
                _pr(2, merged_day=28, branch="lucius/x"),
                _pr(3, merged_day=28, branch="feature/human", labels=["bug"]),
            ],
            "acme/web": [
                _pr(10, merged_day=27, labels=["agent:shipped"]),
                _pr(11, merged_day=27, branch="feature/human"),
            ],
        }
    )
    result = sp.compute_self_proof(["acme/api", "acme/web"], days=7, now=NOW, gh_json=gh)

    by_repo = {r["repo"]: r for r in result["per_repo"]}
    assert by_repo["acme/api"]["merged_total"] == 3
    assert by_repo["acme/api"]["agent_shipped"] == 2
    assert by_repo["acme/api"]["share_pct"] == pytest.approx(66.7, abs=0.05)
    assert by_repo["acme/web"]["agent_shipped"] == 1
    assert by_repo["acme/web"]["merged_total"] == 2
    assert by_repo["acme/web"]["share_pct"] == 50.0

    agg = result["aggregate"]
    assert agg["merged_total"] == 5
    assert agg["agent_shipped"] == 3
    assert agg["share_pct"] == 60.0
    assert agg["repos_counted"] == 2
    assert agg["repos_with_agent_work"] == 2
    assert "shipped 3 of 5 merged PRs (60%)" in result["headline"]
    assert "60% of merged PRs" in result["sentence"]


def test_prs_outside_window_are_excluded():
    # A PR merged 20 days ago is outside a 7-day window and must not count.
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, labels=["agent:authored"]),
                _pr(2, merged_day=10, labels=["agent:authored"]),
            ]
        }
    )
    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    row = result["per_repo"][0]
    assert row["merged_total"] == 1
    assert row["agent_shipped"] == 1


def test_empty_window_reports_none_not_zero_percent():
    # An idle repo has no share to quote. share_pct must be None and the repo
    # flagged no_data, so no fabricated "0% shipped by Alfred" is emitted.
    gh = _gh_for({"acme/api": []})
    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    row = result["per_repo"][0]
    assert row["merged_total"] == 0
    assert row["share_pct"] is None
    assert row["no_data"] is True
    assert result["aggregate"]["share_pct"] is None
    assert "No merged PRs" in result["headline"]
    assert "No merged PRs" in result["sentence"]


def test_failed_repo_is_recorded_not_counted():
    # A gh failure for one repo is recorded in errors and excluded from the
    # aggregate; the healthy repo still produces an honest stat.
    gh = _gh_for(
        {
            "acme/api": [_pr(1, merged_day=28, labels=["agent:authored"])],
            "acme/flaky": None,
        }
    )
    result = sp.compute_self_proof(["acme/api", "acme/flaky"], days=7, now=NOW, gh_json=gh)
    assert result["errors"] == ["acme/flaky"]
    by_repo = {r["repo"]: r for r in result["per_repo"]}
    assert by_repo["acme/flaky"]["errored"] is True
    assert result["aggregate"]["merged_total"] == 1
    assert result["aggregate"]["agent_shipped"] == 1
    assert result["aggregate"]["share_pct"] == 100.0


def test_custom_label_env_override(monkeypatch):
    monkeypatch.setenv("ALFRED_SHIPPED_AGENT_LABELS", "mycorp:bot")
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, labels=["mycorp:bot"], branch="feature/x"),
                _pr(2, merged_day=28, labels=["agent:authored"], branch="feature/x"),
            ]
        }
    )
    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    # Only the custom label counts now; the default agent:authored no longer
    # qualifies because the env override replaces the default set.
    assert result["aggregate"]["agent_shipped"] == 1
    assert result["aggregate"]["merged_total"] == 2


# --------------------------------------------------------------------------
# repo resolution
# --------------------------------------------------------------------------


def test_resolve_repos_explicit_wins(monkeypatch):
    monkeypatch.setenv("ALFRED_SELF_PROOF_REPOS", "env/one")
    assert sp.resolve_repos(["a/b", "c/d"]) == ["a/b", "c/d"]


def test_resolve_repos_direct_env(monkeypatch):
    monkeypatch.setenv("ALFRED_SELF_PROOF_REPOS", "a/b, c/d , a/b")
    assert sp.resolve_repos() == ["a/b", "c/d"]


def test_resolve_repos_self_plus_shipped(monkeypatch):
    monkeypatch.setenv("ALFRED_SELF_PROOF_SELF_REPO", "luminik-io/alfred-os")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "acme/api, acme/web")
    assert sp.resolve_repos() == [
        "luminik-io/alfred-os",
        "acme/api",
        "acme/web",
    ]


def test_resolve_repos_empty_when_unconfigured():
    assert sp.resolve_repos() == []

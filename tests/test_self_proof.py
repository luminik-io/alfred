#!/usr/bin/env python3
"""Tests for lib/self_proof.py: the quotable self-proof stat.

The stat is "X% of merged PRs were shipped by Alfred agents" per repo and in
aggregate. All GitHub access is stubbed via an injected ``gh_json`` callable,
so these run offline and deterministically.

Correctness properties under test (this number is quoted publicly, so both
sides of the ratio must be unimpeachable):

* Numerator: ONLY merged PRs carrying an exact Alfred provenance label count.
  A codename-looking branch, a stale ``automerge/`` branch, or a near-miss
  label (``not-agent:authored``) can never inflate it.
* Denominator: every merged PR in the window, fetched via per-UTC-day
  ``merged:`` search windows and de-duplicated, so it is never truncated by a
  single page cap. A genuinely capped day flags the repo and excludes it from
  the aggregate instead of quoting a wrong share.
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

    ``repo_prs`` maps ``owner/name`` -> list of PR rows returned for EVERY day
    window (the fetch de-duplicates by number, so this is safe). A repo mapped
    to ``None`` simulates a gh failure (returns ``None``).
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


def _search_from(args) -> str:
    """The from-date of the ``merged:>=A merged:<B`` qualifier in a gh argv."""
    qualifier = args[args.index("--search") + 1]
    return qualifier.split("merged:>=")[1].split()[0]


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
# agent-shipped detection: label-authoritative, exact-match
# --------------------------------------------------------------------------


def test_agent_shipped_by_provenance_label():
    assert sp.pr_is_agent_shipped(_pr(1, merged_day=28, labels=["agent:authored"]))
    assert sp.pr_is_agent_shipped(_pr(2, merged_day=28, labels=["agent:shipped"]))


def test_branch_prefix_alone_does_not_qualify():
    # A human PR pushed to a codename-looking or stale automerge branch must
    # NOT count: the label is the authoritative provenance signal, matching
    # shipped_board. The branch is recorded as corroborating evidence only.
    for branch in ("lucius/fix-bug", "batman/rollout", "automerge/dep-bump"):
        pr = _pr(3, merged_day=28, branch=branch)
        assert not sp.pr_is_agent_shipped(pr)
        assert f"branch:{branch}" in sp.pr_agent_evidence(pr)


def test_near_miss_labels_do_not_qualify():
    # Substring lookalikes must not count; only exact label names qualify.
    for label in ("not-agent:authored", "agent:authored-needed", "agent:authoredx"):
        assert not sp.pr_is_agent_shipped(_pr(4, merged_day=28, labels=[label]))


def test_label_plus_branch_yields_both_evidence_kinds():
    pr = _pr(5, merged_day=28, labels=["agent:authored"], branch="lucius/x")
    assert sp.pr_is_agent_shipped(pr)
    evidence = sp.pr_agent_evidence(pr)
    assert "label:agent:authored" in evidence
    assert "branch:lucius/x" in evidence


def test_human_pr_is_not_agent_shipped():
    assert not sp.pr_is_agent_shipped(
        _pr(6, merged_day=28, branch="feature/manual", labels=["bug"])
    )


def test_excluded_author_never_counts_even_with_label():
    # dependabot (and friends) never count, even when a label sync stamped the
    # provenance label onto a bot PR.
    assert not sp.pr_is_agent_shipped(
        _pr(7, merged_day=28, labels=["agent:authored"], author="dependabot[bot]")
    )


# --------------------------------------------------------------------------
# per-repo + aggregate computation
# --------------------------------------------------------------------------


def test_share_and_aggregate_across_repos():
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, labels=["agent:authored"]),
                _pr(2, merged_day=28, labels=["agent:done"], branch="lucius/x"),
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


def test_branch_only_prs_stay_in_denominator_not_numerator():
    # The inflation case from review: human PRs on codename branches. They
    # must count as merged (denominator) but never as agent-shipped.
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, labels=["agent:authored"]),
                _pr(2, merged_day=28, branch="lucius/human-lookalike"),
                _pr(3, merged_day=28, branch="automerge/stale"),
            ]
        }
    )
    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    row = result["per_repo"][0]
    assert row["merged_total"] == 3
    assert row["agent_shipped"] == 1
    assert row["share_pct"] == pytest.approx(33.3, abs=0.05)


def test_zero_agent_prs_avoid_headline_zero_percent():
    gh = _gh_for(
        {
            "acme/api": [
                _pr(1, merged_day=28, branch="feature/human"),
                _pr(2, merged_day=28, branch="fix/human"),
            ]
        }
    )
    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    assert result["aggregate"]["merged_total"] == 2
    assert result["aggregate"]["agent_shipped"] == 0
    assert result["aggregate"]["share_pct"] == 0
    assert "No public agent-attributed PRs among 2 merged PRs" in result["headline"]
    assert "0%" not in result["headline"]
    assert "0%" not in result["sentence"]


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
# pagination + cap honesty
# --------------------------------------------------------------------------


def test_day_qualifiers_cover_whole_window():
    start = NOW - sp.timedelta(days=7)
    qualifiers = sp._day_qualifiers(start, NOW)
    # 2026-06-23 .. 2026-06-30 inclusive: 8 UTC calendar days.
    assert len(qualifiers) == 8
    assert qualifiers[0] == "merged:>=2026-06-23 merged:<2026-06-24"
    assert qualifiers[-1] == "merged:>=2026-06-30 merged:<2026-07-01"


def test_denominator_counts_past_a_single_page():
    # 3 UTC days x 150 merged PRs with a 200-row page limit. A single
    # unwindowed `gh pr list --limit 200` would truncate to 200 rows; the
    # day-windowed fetch must count all 450, with the agent subset intact.
    days_rows: dict[str, list[dict]] = {}
    number = 0
    for day in (27, 28, 29):
        rows = []
        for i in range(150):
            number += 1
            labels = ["agent:authored"] if i % 3 == 0 else []
            rows.append(_pr(number, merged_day=day, labels=labels))
        days_rows[f"2026-06-{day:02d}"] = rows

    def gh(args, **kwargs):
        return days_rows.get(_search_from(args), [])

    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh, limit=200)
    row = result["per_repo"][0]
    assert row["capped"] is False
    assert row["merged_total"] == 450
    assert row["agent_shipped"] == 150
    assert result["aggregate"]["share_pct"] == pytest.approx(33.3, abs=0.05)


def test_duplicate_rows_across_windows_are_deduped():
    # The same PR returned by two day windows must count once.
    pr = _pr(1, merged_day=28, labels=["agent:authored"])

    def gh(args, **kwargs):
        return [pr] if _search_from(args) in ("2026-06-27", "2026-06-28") else []

    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh)
    row = result["per_repo"][0]
    assert row["merged_total"] == 1
    assert row["agent_shipped"] == 1


def test_capped_day_window_excludes_repo_from_share():
    # When a day window returns as many rows as the limit, rows beyond the cap
    # are unknowable, so the repo must be flagged capped, report no share, and
    # be excluded from the aggregate instead of quoting a truncated ratio.
    rows = [_pr(i, merged_day=28, labels=["agent:authored"]) for i in range(1, 6)]

    def gh(args, **kwargs):
        return rows if _search_from(args) == "2026-06-28" else []

    result = sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh, limit=5)
    row = result["per_repo"][0]
    assert row["capped"] is True
    assert row["share_pct"] is None
    assert row["merged_total"] == 0
    assert result["capped"] == ["acme/api"]
    assert result["aggregate"]["merged_total"] == 0
    assert result["aggregate"]["share_pct"] is None


def test_capped_repo_does_not_poison_healthy_repo():
    capped_rows = [_pr(i, merged_day=28) for i in range(1, 4)]
    healthy_rows = [_pr(10, merged_day=28, labels=["agent:authored"])]

    def gh(args, **kwargs):
        repo = args[args.index("--repo") + 1]
        if repo == "acme/firehose":
            return capped_rows if _search_from(args) == "2026-06-28" else []
        return healthy_rows if _search_from(args) == "2026-06-28" else []

    result = sp.compute_self_proof(
        ["acme/firehose", "acme/api"], days=7, now=NOW, gh_json=gh, limit=3
    )
    assert result["capped"] == ["acme/firehose"]
    assert result["aggregate"]["merged_total"] == 1
    assert result["aggregate"]["agent_shipped"] == 1
    assert result["aggregate"]["share_pct"] == 100.0


def test_fetch_uses_merged_search_qualifier_and_limit():
    # The gh query itself must be date-scoped (merged:) and carry the limit,
    # so the page cap applies per day, not to the whole window.
    seen_args: list[list[str]] = []

    def gh(args, **kwargs):
        seen_args.append(list(args))
        return []

    sp.compute_self_proof(["acme/api"], days=7, now=NOW, gh_json=gh, limit=42)
    assert seen_args, "expected gh queries"
    for args in seen_args:
        assert "--search" in args
        assert "merged:>=" in args[args.index("--search") + 1]
        assert args[args.index("--limit") + 1] == "42"


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
    monkeypatch.setenv("ALFRED_SELF_PROOF_SELF_REPO", "luminik-io/alfred")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "acme/api, acme/web")
    assert sp.resolve_repos() == [
        "luminik-io/alfred",
        "acme/api",
        "acme/web",
    ]


def test_resolve_repos_empty_when_unconfigured():
    assert sp.resolve_repos() == []

"""Compute the "quotable self-proof" stat: the share of merged PRs shipped by
Alfred agents, for a configured repo set over a rolling window.

The measurable claim this module produces, in the spirit of Aider's "wrote 70%
of its own code" line, is:

    X% of a repo's merged PRs in the last N days were shipped by Alfred agents

plus a fleet-wide aggregate across every configured repo:

    Alfred agents shipped N merged PRs across M repos in the last N days

Attribution is label-authoritative, matching ``shipped_board._pr_is_agent_shipped``
and the shipped-summary path: a merged PR counts as agent-shipped ONLY when it
carries one of Alfred's provenance labels (``agent:authored`` set on PR open,
plus ``agent:done`` / ``agent:shipped`` and friends), matched EXACTLY, not by
substring. A canonical role branch prefix (``senior-dev/``, ``architect/``, ...)
is recorded as
corroborating evidence for display but never qualifies a PR on its own, so a
human PR pushed to a role-looking branch, a stale ``automerge/`` branch, or a
near-miss label like ``not-agent:authored`` can never inflate the numerator.
Every merged PR in the window is the denominator; the agent-shipped subset is
the numerator. Human and bot PRs stay in the denominator so the percentage is
honest: it is the fleet's real share of the merge stream, not a
filtered-then-counted illusion.

Merged PRs are fetched with a ``merged:`` date-window search qualifier, one
UTC-day sub-window at a time, and de-duplicated by PR number, so the count is
complete for the whole window rather than truncated at a single page. If any
sub-window still returns as many rows as the query limit, the repo is flagged
``capped`` and EXCLUDED from the aggregate instead of contributing a silently
truncated denominator: a wrong percentage is worse than no percentage.

All GitHub access flows through an injectable ``gh_json`` callable so the whole
module is unit-testable with a stubbed shell, exactly like ``shipped_board``.
Repos are resolved from operator config (no hardcoded repo names), so this
module behaves identically in the public OSS twin.

Honesty contract: when a repo has zero merged PRs in the window, its share is
reported as ``None`` (not 0 and not 100) and it is flagged ``no_data`` so a
caller never prints a fabricated "0% shipped by Alfred" for an idle repo. The
aggregate share is likewise ``None`` when the total across all repos is zero.
Errored and capped repos are excluded from the aggregate and reported.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_WINDOW_DAYS = 7
# Per-day-window query limit. The window is split into UTC-day sub-queries, so
# this cap applies to ONE day of one repo's merges, not the whole window. A repo
# merging 500+ PRs in a single day trips the ``capped`` flag and is excluded
# from the aggregate rather than silently undercounted.
_PER_WINDOW_LIMIT = 500

# Alfred's provenance labels, kept in lockstep with shipped_board.py and the
# shipped summary so the self-proof numerator matches what the rest of the
# fleet counts as agent work. Matched EXACTLY (case-insensitive), never by
# substring. Overridable via ALFRED_SHIPPED_AGENT_LABELS.
_DEFAULT_AGENT_SHIPPED_LABELS = (
    "agent:authored",
    "agent:done",
    "agent:shipped",
    "alfred:shipped",
    "shipped-by-alfred",
)

# Agent branch prefixes. Display-only corroboration (see pr_agent_evidence);
# a prefix match NEVER qualifies a PR as agent-shipped on its own.
_DEFAULT_AGENT_BRANCH_PREFIXES = (
    "alfred/",
    "alfred-nightly/",
    "architect/",
    "automerge/",
    "e2e-runner/",
    "fixer/",
    "ops-watch/",
    "planner/",
    "reviewer/",
    "senior-dev/",
    "spec-planner/",
    "test-engineer/",
    "triage/",
)

# PR authors that are never agent work even when carrying an agent-looking
# label (e.g. a label sync gone wrong on a bot bump). Mirrors the site
# emitters' dependabot exclusion.
_DEFAULT_EXCLUDED_AUTHORS = (
    "app/dependabot",
    "dependabot",
    "dependabot[bot]",
)

# Directories where ``gh`` commonly lives, mirrored from shipped_board so a
# bare-PATH host still finds the binary.
_GH_EXTRA_PATH = (
    os.path.expanduser("~/.local/bin"),
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
)


def _csv_env(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if raw:
        return tuple(v.strip().lower() for v in raw.split(",") if v.strip())
    return default


def _shipped_label_hints() -> tuple[str, ...]:
    return _csv_env("ALFRED_SHIPPED_AGENT_LABELS", _DEFAULT_AGENT_SHIPPED_LABELS)


def _shipped_branch_prefixes() -> tuple[str, ...]:
    # Branch prefixes are matched with startswith, so preserve case as authored
    # by the operator; lowercasing here would break a case-sensitive prefix.
    raw = os.environ.get("ALFRED_SHIPPED_AGENT_BRANCH_PREFIXES", "").strip()
    if raw:
        return tuple(v.strip() for v in raw.split(",") if v.strip())
    return _DEFAULT_AGENT_BRANCH_PREFIXES


def _excluded_authors() -> tuple[str, ...]:
    return _csv_env("ALFRED_SELF_PROOF_EXCLUDED_AUTHORS", _DEFAULT_EXCLUDED_AUTHORS)


def _labels(pr: dict) -> list[str]:
    out: list[str] = []
    for lab in pr.get("labels") or []:
        name = lab.get("name") if isinstance(lab, dict) else lab
        if isinstance(name, str) and name:
            out.append(name.lower())
    return out


def _author_login(pr: dict) -> str:
    author = pr.get("author") or {}
    login = author.get("login") if isinstance(author, dict) else None
    return (login or "").strip().lower()


def pr_is_agent_shipped(pr: dict) -> bool:
    """True only when a merged PR carries an Alfred provenance label.

    The provenance label (``agent:authored``, applied by the fleet when it
    opens the PR, plus ``agent:done`` / ``agent:shipped`` set on merge) is the
    authoritative signal, matched EXACTLY against the PR's label names, the
    same rule ``shipped_board._pr_is_agent_shipped`` and the shipped summary
    use. Substring matches (``not-agent:authored``, ``agent:authored-needed``)
    do NOT qualify. A branch prefix does NOT qualify on its own either; it is
    recorded by :func:`pr_agent_evidence` for display only, so a human PR on a
    codename-looking branch or a stale ``automerge/`` branch can never inflate
    the numerator. Excluded authors (dependabot and friends) never count even
    when labelled.
    """
    if _author_login(pr) in _excluded_authors():
        return False
    return bool(set(_labels(pr)) & set(_shipped_label_hints()))


def pr_agent_evidence(pr: dict) -> list[str]:
    """All agent evidence on a PR, for display: labels qualify, branches corroborate."""
    evidence: list[str] = []
    label_hints = set(_shipped_label_hints())
    for label in _labels(pr):
        if label in label_hints:
            evidence.append(f"label:{label}")
    branch = (pr.get("headRefName") or "").strip()
    lowered = branch.lower()
    prefixes = tuple(p.lower() for p in _shipped_branch_prefixes())
    if lowered and any(lowered.startswith(prefix) for prefix in prefixes):
        evidence.append(f"branch:{lowered}")
    return evidence


# --------------------------------------------------------------------------
# gh access (mirrors shipped_board so the module stands alone)
# --------------------------------------------------------------------------


def _gh_bin() -> str:
    configured = os.environ.get("ALFRED_GH_BIN") or os.environ.get("GH_BIN")
    if configured:
        return configured
    search = os.pathsep.join((*_GH_EXTRA_PATH, os.environ.get("PATH", "")))
    return shutil.which("gh", path=search) or "gh"


def _gh_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in reversed(_GH_EXTRA_PATH):
        if extra not in parts:
            parts.insert(0, extra)
    env["PATH"] = os.pathsep.join(parts)
    return env


def default_gh_json(args: list[str], *, timeout: int = 30) -> Any:
    """Run a ``gh`` command with ``--json`` output and return parsed JSON.

    Returns ``None`` on any failure (missing gh, auth error, rate limit, bad
    repo) so a single flaky repo never breaks the whole computation.
    """
    try:
        proc = subprocess.run(
            [_gh_bin(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_gh_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# repo resolution
# --------------------------------------------------------------------------


def _self_repo() -> str | None:
    """The repo Alfred runs on, so the default set always includes the fleet.

    Reads ALFRED_SELF_PROOF_SELF_REPO, then the canonical public slug. Returns
    ``None`` when neither is set so ``resolve_repos`` simply falls through to
    the configured shipped-repo list.
    """
    explicit = os.environ.get("ALFRED_SELF_PROOF_SELF_REPO", "").strip()
    if explicit:
        return explicit
    canonical = os.environ.get("ALFRED_SELF_REPO", "").strip()
    return canonical or None


def resolve_repos(explicit: list[str] | None = None) -> list[str]:
    """Resolve the repo set for the self-proof stat, config-driven.

    Precedence: explicit arg -> ``ALFRED_SELF_PROOF_REPOS`` ->
    (self repo + ``ALFRED_SHIPPED_REPOS``) -> ``ALFRED_SHIPPED_REPOS`` alone.
    De-duplicated, order-preserving. No repo names are hardcoded, so the public
    twin behaves identically.
    """
    if explicit:
        return _dedupe([r.strip() for r in explicit if r.strip()])

    direct = os.environ.get("ALFRED_SELF_PROOF_REPOS", "").strip()
    if direct:
        return _dedupe([r.strip() for r in direct.split(",") if r.strip()])

    repos: list[str] = []
    self_repo = _self_repo()
    if self_repo:
        repos.append(self_repo)
    shipped = os.environ.get("ALFRED_SHIPPED_REPOS", "").strip()
    if shipped:
        repos.extend(r.strip() for r in shipped.split(",") if r.strip())
    return _dedupe(repos)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# computation
# --------------------------------------------------------------------------


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _share_pct(agent: int, total: int) -> float | None:
    """Agent share as a percentage, or ``None`` when there is no data.

    Returning ``None`` for a zero denominator is the honesty contract: an idle
    repo has no share to quote, so callers render "no data" rather than a
    fabricated 0% or 100%.
    """
    if total <= 0:
        return None
    return round(100.0 * agent / total, 1)


def _day_qualifiers(start: datetime, end: datetime) -> list[str]:
    """UTC-day ``merged:`` search qualifiers covering ``[start, end]``.

    One qualifier per UTC calendar day, ``merged:>=D merged:<D+1``, the same
    day-slicing the shipped summary uses. Slicing the window into days keeps
    each query far under any page cap and lets the caller detect a genuinely
    capped day instead of silently losing rows past a single page. Date
    qualifiers are day-granular; the exact ``[start, end]`` timestamp filter is
    applied locally on ``mergedAt`` by the caller.
    """
    qualifiers: list[str] = []
    day = start.astimezone(UTC).date()
    last = end.astimezone(UTC).date()
    while day <= last:
        next_day = day + timedelta(days=1)
        qualifiers.append(f"merged:>={day.isoformat()} merged:<{next_day.isoformat()}")
        day = next_day
    return qualifiers


def _fetch_repo(
    repo: str,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    gh_json: Callable[..., Any],
) -> dict[str, Any]:
    """Compute one repo's agent-vs-total merged-PR counts in ``[start, end]``.

    Queries one UTC-day sub-window at a time with a ``merged:`` search
    qualifier and de-duplicates by PR number, so the denominator covers the
    whole window instead of one truncated page. Returns a per-repo record.
    ``errored`` is True when any gh query failed; ``capped`` is True when any
    day window returned as many rows as the query limit (the counts beyond the
    cap are unknowable, so the caller must not quote a share from them). Pure
    per-repo work, safe to run concurrently.
    """
    seen: dict[int, dict] = {}
    errored = False
    capped = False
    for qualifier in _day_qualifiers(start, end):
        prs = gh_json(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "merged",
                "--search",
                qualifier,
                "--limit",
                str(limit),
                "--json",
                "number,title,url,author,mergedAt,labels,headRefName",
            ]
        )
        if prs is None:
            errored = True
            continue
        if len(prs) >= limit:
            capped = True
        for pr in prs:
            number = pr.get("number")
            if isinstance(number, int):
                seen[number] = pr

    merged_total = 0
    agent_shipped = 0
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    for pr in seen.values():
        merged = _parse_ts(pr.get("mergedAt"))
        if not merged:
            continue
        ts = merged.timestamp()
        if ts < start_ts or ts > end_ts:
            continue
        merged_total += 1
        if pr_is_agent_shipped(pr):
            agent_shipped += 1

    unusable = errored or capped
    return {
        "repo": repo,
        "merged_total": 0 if unusable else merged_total,
        "agent_shipped": 0 if unusable else agent_shipped,
        "share_pct": None if unusable else _share_pct(agent_shipped, merged_total),
        "errored": errored,
        "capped": capped,
        "no_data": unusable or merged_total == 0,
    }


def compute_self_proof(
    repos: list[str],
    *,
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int = _PER_WINDOW_LIMIT,
    now: datetime | None = None,
    gh_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Compute the self-proof stat across ``repos`` over a rolling window.

    Returns a JSON-ready dict with per-repo rows and an aggregate::

        {
          "generated_at": "...ISO8601...",
          "window_days": 7,
          "repos": [...],
          "per_repo": [
            {"repo": "owner/name", "merged_total": 12, "agent_shipped": 9,
             "share_pct": 75.0, "no_data": false, "errored": false,
             "capped": false},
            ...
          ],
          "aggregate": {
            "merged_total": 40, "agent_shipped": 30, "share_pct": 75.0,
            "repos_counted": 3, "repos_with_agent_work": 2
          },
          "errors": ["owner/flaky"],
          "capped": ["owner/firehose"],
          "headline": "Alfred agents shipped 30 of 40 merged PRs (75%) across 3 repos in the last 7 days.",
          "sentence": "75% of merged PRs across 3 repos were shipped by Alfred agents in the last 7 days."
        }

    Repos are queried concurrently, one UTC-day search window at a time (see
    ``_fetch_repo``), so the denominator is complete for the window. A failing
    repo is recorded in ``errors`` and a page-capped repo in ``capped``; both
    are excluded from the aggregate rather than contributing wrong counts,
    because this number is quoted publicly and a truncated share is worse than
    a smaller repo set. When the aggregate denominator is zero, ``share_pct``
    is ``None`` and the sentences say so honestly.
    """
    now = now or datetime.now(UTC)
    fetch = gh_json or default_gh_json
    start = now - timedelta(days=max(1, days))

    per_repo: list[dict[str, Any]] = []
    errors: list[str] = []
    capped: list[str] = []

    if repos:
        max_workers = min(len(repos), 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _fetch_repo, repo, start=start, end=now, limit=limit, gh_json=fetch
                ): repo
                for repo in repos
            }
            results: dict[str, dict[str, Any]] = {}
            for fut in concurrent.futures.as_completed(futures):
                repo = futures[fut]
                try:
                    results[repo] = fut.result()
                except Exception:
                    results[repo] = {
                        "repo": repo,
                        "merged_total": 0,
                        "agent_shipped": 0,
                        "share_pct": None,
                        "errored": True,
                        "capped": False,
                        "no_data": True,
                    }
        # Preserve the caller's repo order for stable output.
        for repo in repos:
            row = results[repo]
            if row.get("errored"):
                errors.append(repo)
            if row.get("capped"):
                capped.append(repo)
            per_repo.append(
                {
                    "repo": row["repo"],
                    "merged_total": row["merged_total"],
                    "agent_shipped": row["agent_shipped"],
                    "share_pct": row["share_pct"],
                    "no_data": row["no_data"],
                    "errored": row["errored"],
                    "capped": row.get("capped", False),
                }
            )

    def _usable(row: dict[str, Any]) -> bool:
        return not row["errored"] and not row["capped"]

    agent_total = sum(r["agent_shipped"] for r in per_repo if _usable(r))
    merged_total = sum(r["merged_total"] for r in per_repo if _usable(r))
    repos_counted = sum(1 for r in per_repo if _usable(r) and r["merged_total"] > 0)
    repos_with_agent_work = sum(1 for r in per_repo if _usable(r) and r["agent_shipped"] > 0)
    aggregate = {
        "merged_total": merged_total,
        "agent_shipped": agent_total,
        "share_pct": _share_pct(agent_total, merged_total),
        "repos_counted": repos_counted,
        "repos_with_agent_work": repos_with_agent_work,
    }

    return {
        "generated_at": now.astimezone(UTC).isoformat(),
        "window_days": days,
        "repos": list(repos),
        "per_repo": per_repo,
        "aggregate": aggregate,
        "errors": sorted(set(errors)),
        "capped": sorted(set(capped)),
        "headline": _headline(aggregate, days),
        "sentence": _sentence(aggregate, days),
    }


def _repo_word(count: int) -> str:
    return "repo" if count == 1 else "repos"


def _headline(aggregate: dict[str, Any], days: int) -> str:
    """A re-quotable one-liner for Slack, README, or a talk slide.

    Never fabricates: when there is no merged-PR data in the window it says so
    plainly instead of quoting a 0% share.
    """
    merged = int(aggregate["merged_total"])
    agent = int(aggregate["agent_shipped"])
    repos_counted = int(aggregate["repos_counted"])
    share = aggregate["share_pct"]
    if merged <= 0 or share is None:
        return f"No merged PRs in the configured repos in the last {days} days yet."
    if agent <= 0:
        return (
            f"No public agent-attributed PRs among {merged} merged PRs "
            f"across {repos_counted} {_repo_word(repos_counted)} "
            f"in the last {days} days yet."
        )
    return (
        f"Alfred agents shipped {agent} of {merged} merged PRs "
        f"({share:g}%) across {repos_counted} {_repo_word(repos_counted)} "
        f"in the last {days} days."
    )


def _sentence(aggregate: dict[str, Any], days: int) -> str:
    """The Aider-style share sentence, honest on empty data."""
    merged = int(aggregate["merged_total"])
    repos_counted = int(aggregate["repos_counted"])
    share = aggregate["share_pct"]
    if merged <= 0 or share is None:
        return f"No merged PRs to measure in the last {days} days yet."
    if int(aggregate["agent_shipped"]) <= 0:
        return (
            f"No public agent-attributed PRs among {merged} merged PRs "
            f"across {repos_counted} {_repo_word(repos_counted)} "
            f"in the last {days} days yet."
        )
    return (
        f"{share:g}% of merged PRs across {repos_counted} "
        f"{_repo_word(repos_counted)} were shipped by Alfred agents "
        f"in the last {days} days."
    )

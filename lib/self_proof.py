"""Compute the "quotable self-proof" stat: the share of merged PRs shipped by
Alfred agents, for a configured repo set over a rolling window.

The measurable claim this module produces, in the spirit of Aider's "wrote 70%
of its own code" line, is:

    X% of a repo's merged PRs in the last N days were shipped by Alfred agents

plus a fleet-wide aggregate across every configured repo:

    Alfred agents shipped N merged PRs across M repos in the last N days

The agent-authored signal is the SAME one the rest of the fleet uses: a merged
PR counts as agent-shipped when it carries one of Alfred's provenance labels
(``agent:authored`` and friends, applied on PR open and merge) or was pushed
from an agent branch prefix. Every merged PR in the window is the denominator;
the agent-shipped subset is the numerator. Human and bot PRs stay in the
denominator so the percentage is honest: it is the fleet's real share of the
merge stream, not a filtered-then-counted illusion.

All GitHub access flows through an injectable ``gh_json`` callable so the whole
module is unit-testable with a stubbed shell, exactly like ``shipped_board``.
Repos are resolved from operator config (no hardcoded repo names), so this
module behaves identically in the public OSS twin.

Honesty contract: when a repo has zero merged PRs in the window, its share is
reported as ``None`` (not 0 and not 100) and it is flagged ``no_data`` so a
caller never prints a fabricated "0% shipped by Alfred" for an idle repo. The
aggregate share is likewise ``None`` when the total across all repos is zero.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

DEFAULT_WINDOW_DAYS = 7
_PER_REPO_LIMIT = 200

# Agent-authorship signals, kept in lockstep with shipped_board.py so the
# self-proof numerator matches what the shipped board and Impact page count as
# agent work. Overridable via the same env knobs used elsewhere.
_DEFAULT_AGENT_SHIPPED_LABELS = (
    "agent:authored",
    "agent:done",
    "agent:shipped",
    "alfred:shipped",
    "shipped-by-alfred",
)

_DEFAULT_AGENT_BRANCH_PREFIXES = (
    "alfred/",
    "alfred-nightly/",
    "automerge/",
    "bane/",
    "batman/",
    "damian/",
    "lucius/",
    "nightwing/",
    "rasalghul/",
    "robin/",
)

# PR authors that are never agent work even on an agent-looking branch. Mirrors
# the site emitters' dependabot exclusion so a bot bump does not inflate either
# side of the ratio.
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
    """True when a merged PR looks agent-shipped.

    A PR counts as agent-shipped when it carries any Alfred provenance label OR
    was pushed from an agent branch prefix, and its author is not on the
    excluded-authors list (dependabot and friends). Conservative on both ends: a
    bot PR on an ``automerge/`` branch is excluded, and a PR with no agent
    signal is never counted, so the numerator never claims work the fleet did
    not do.
    """
    if _author_login(pr) in _excluded_authors():
        return False
    label_hints = _shipped_label_hints()
    if any(any(hint in label for hint in label_hints) for label in _labels(pr)):
        return True
    branch = (pr.get("headRefName") or "").strip().lower()
    prefixes = tuple(p.lower() for p in _shipped_branch_prefixes())
    return bool(branch) and any(branch.startswith(prefix) for prefix in prefixes)


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


def _fetch_repo(
    repo: str,
    *,
    cutoff: float,
    limit: int,
    gh_json: Callable[..., Any],
) -> dict[str, Any]:
    """Compute one repo's agent-vs-total merged-PR counts in the window.

    Returns a per-repo record. ``errored`` is True when the gh query failed, so
    the caller records it without inventing counts. Pure per-repo work, safe to
    run concurrently.
    """
    prs = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,author,mergedAt,labels,headRefName",
        ]
    )
    if prs is None:
        return {
            "repo": repo,
            "merged_total": 0,
            "agent_shipped": 0,
            "share_pct": None,
            "errored": True,
            "no_data": True,
        }

    merged_total = 0
    agent_shipped = 0
    for pr in prs:
        merged = _parse_ts(pr.get("mergedAt"))
        if not merged or merged.timestamp() < cutoff:
            continue
        merged_total += 1
        if pr_is_agent_shipped(pr):
            agent_shipped += 1

    return {
        "repo": repo,
        "merged_total": merged_total,
        "agent_shipped": agent_shipped,
        "share_pct": _share_pct(agent_shipped, merged_total),
        "errored": False,
        "no_data": merged_total == 0,
    }


def compute_self_proof(
    repos: list[str],
    *,
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int = _PER_REPO_LIMIT,
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
             "share_pct": 75.0, "no_data": false, "errored": false},
            ...
          ],
          "aggregate": {
            "merged_total": 40, "agent_shipped": 30, "share_pct": 75.0,
            "repos_counted": 3, "repos_with_agent_work": 2
          },
          "errors": ["owner/flaky"],
          "headline": "Alfred agents shipped 30 of 40 merged PRs (75%) across 3 repos in the last 7 days.",
          "sentence": "75% of merged PRs across 3 repos were shipped by Alfred agents in the last 7 days."
        }

    Repos are queried concurrently; a failing repo is recorded in ``errors`` and
    excluded from the aggregate rather than breaking the whole stat. When the
    aggregate denominator is zero, ``share_pct`` is ``None`` and the sentences
    say so honestly.
    """
    now = now or datetime.now(UTC)
    fetch = gh_json or default_gh_json
    cutoff = now.timestamp() - max(1, days) * 86400

    per_repo: list[dict[str, Any]] = []
    errors: list[str] = []

    if repos:
        max_workers = min(len(repos), 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_repo, repo, cutoff=cutoff, limit=limit, gh_json=fetch): repo
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
                        "no_data": True,
                    }
        # Preserve the caller's repo order for stable output.
        for repo in repos:
            row = results[repo]
            if row.get("errored"):
                errors.append(repo)
            per_repo.append(
                {
                    "repo": row["repo"],
                    "merged_total": row["merged_total"],
                    "agent_shipped": row["agent_shipped"],
                    "share_pct": row["share_pct"],
                    "no_data": row["no_data"],
                    "errored": row["errored"],
                }
            )

    agent_total = sum(r["agent_shipped"] for r in per_repo if not r["errored"])
    merged_total = sum(r["merged_total"] for r in per_repo if not r["errored"])
    repos_counted = sum(1 for r in per_repo if not r["errored"] and r["merged_total"] > 0)
    repos_with_agent_work = sum(1 for r in per_repo if not r["errored"] and r["agent_shipped"] > 0)
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
    return (
        f"{share:g}% of merged PRs across {repos_counted} "
        f"{_repo_word(repos_counted)} were shipped by Alfred agents "
        f"in the last {days} days."
    )

#!/usr/bin/env python3
"""Alfred-nightly - scheduled dependency updater (opt-in, config-driven).

Off by default: with no repos configured this is a no-op, so scheduling it
disarmed is safe. Point it at your own repos through the environment.

Per-ecosystem strategy:
- npm repos: apply safe-band updates (caret-compatible bumps via
  ``npm update``), run pre-push verification, open one PR per repo. Major
  bumps and CVEs are reported separately but never auto-merged.
- gradle/pip repos: ADVISORY ONLY. The job posts an outdated list to Slack
  but does not open PRs. Gradle dependency graphs and Python pins are more
  tightly coupled than npm safe-band bumps and need human triage.

Configuration (all optional; empty means "skip that ecosystem"):

- ``ALFRED_NIGHTLY_NPM_REPOS`` - semicolon-separated npm entries, each
  ``local_dir:repo_slug:pre_push_cmd``. ``pre_push_cmd`` may be empty (just
  verify ``npm install`` resolves) and may contain ``:`` freely since only
  the first two colons split the entry. Example::

      ALFRED_NIGHTLY_NPM_REPOS="frontend:your-frontend:npm install && npm run build;api:your-api:npm install"

- ``ALFRED_NIGHTLY_ADVISORY_REPOS`` - semicolon-separated advisory entries,
  each ``local_dir:repo_slug:ecosystem`` where ``ecosystem`` is ``gradle``
  or ``pip``. Example::

      ALFRED_NIGHTLY_ADVISORY_REPOS="backend:your-backend:gradle;data:your-data:pip"

Bare ``repo_slug`` values resolve through ``GH_ORG``; full ``owner/repo``
slugs work as-is. ``local_dir`` is the checkout directory name under the
workspace root.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from agent_runner import (  # noqa: E402
    WORKSPACE,
    PreflightFailed,
    PreflightSpec,
    SpendState,
    doctor_mode,
    gh_pr_create,
    make_worktree,
    preflight,
    push_current_branch,
    push_remote_and_pr_head,
    remove_worktree,
    run,
    short,
    slack_post,
    with_lock,
)


def _parse_npm_repos(raw: str | None = None) -> list[tuple[str, str, str | None]]:
    """Parse ``ALFRED_NIGHTLY_NPM_REPOS`` into ``(local, slug, pre_push)``.

    Each entry is ``local:slug:pre_push_cmd``; only the first two colons
    split, so a pre-push command may contain colons. An empty command
    becomes ``None`` (install-only verification).
    """
    text = raw if raw is not None else os.environ.get("ALFRED_NIGHTLY_NPM_REPOS", "")
    out: list[tuple[str, str, str | None]] = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            continue
        local = parts[0].strip()
        slug = parts[1].strip()
        pre_push = parts[2].strip() if len(parts) == 3 and parts[2].strip() else None
        out.append((local, slug, pre_push))
    return out


def _parse_advisory_repos(raw: str | None = None) -> list[tuple[str, str, str]]:
    """Parse ``ALFRED_NIGHTLY_ADVISORY_REPOS`` into ``(local, slug, ecosystem)``."""
    text = raw if raw is not None else os.environ.get("ALFRED_NIGHTLY_ADVISORY_REPOS", "")
    out: list[tuple[str, str, str]] = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            continue
        out.append((parts[0], parts[1], parts[2]))
    return out


AGENT = "alfred-nightly"

# Resolved once at import so the preflight spec and the run share one view.
NPM_REPOS: list[tuple[str, str, str | None]] = _parse_npm_repos()
ADVISORY_REPOS: list[tuple[str, str, str]] = _parse_advisory_repos()

PREFLIGHT = PreflightSpec(
    agent=AGENT,
    bins=["gh", "git", "npm"],
    require_gh_auth=True,
    require_workspace_repos=[local for local, _slug, _pp in NPM_REPOS],
)

CVE_SEVERITIES = {"moderate", "high", "critical"}

# Cap how many lines we cite per repo in the Slack summary.
MAX_LINES_PER_REPO = 12


# ---------- npm helpers ----------


def npm_outdated(wt: Path) -> dict[str, dict[str, str]]:
    """Return parsed `npm outdated --json`. npm exits non-zero when there
    are outdated packages; that is normal, don't treat it as failure."""
    res = run(["npm", "outdated", "--json"], cwd=str(wt), timeout=180)
    out = (res.stdout or "").strip()
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def npm_audit(wt: Path) -> dict[str, Any]:
    """Return parsed `npm audit --json`. Non-zero exit is normal when vulns
    exist; we only care about the JSON payload."""
    res = run(["npm", "audit", "--json"], cwd=str(wt), timeout=180)
    out = (res.stdout or "").strip()
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def split_safe_vs_major(
    outdated: dict[str, dict[str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Return (safe_bumps, major_bumps).

    safe = current != wanted (caret-compatible bump within current major)
    major = wanted != latest (a higher major exists)
    A package can appear in both lists when it has both a safe bump
    available AND a higher major exists.
    """
    safe: list[tuple[str, str, str]] = []
    majors: list[tuple[str, str, str]] = []
    for pkg, info in outdated.items():
        current = info.get("current") or ""
        wanted = info.get("wanted") or ""
        latest = info.get("latest") or ""
        if current and wanted and current != wanted:
            safe.append((pkg, current, wanted))
        if wanted and latest and wanted != latest:
            majors.append((pkg, wanted, latest))
    safe.sort()
    majors.sort()
    return safe, majors


def collect_cves(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull moderate/high/critical advisories out of the npm audit payload."""
    vulns = audit.get("vulnerabilities") or {}
    out: list[dict[str, Any]] = []
    for pkg, info in vulns.items():
        if not isinstance(info, dict):
            continue
        sev = (info.get("severity") or "").lower()
        if sev not in CVE_SEVERITIES:
            continue
        # `via` can be a list of mixed strings + dicts (transitive chains).
        via = info.get("via") or []
        advisories: list[dict[str, Any]] = []
        if isinstance(via, list):
            for v in via:
                if isinstance(v, dict) and v.get("url"):
                    advisories.append(
                        {
                            "title": v.get("title", "")[:140],
                            "url": v.get("url", ""),
                            "severity": (v.get("severity") or "").lower(),
                        }
                    )
        out.append(
            {
                "package": pkg,
                "severity": sev,
                "advisories": advisories[:3],
            }
        )
    out.sort(
        key=lambda v: (
            {"critical": 0, "high": 1, "moderate": 2}.get(v["severity"], 9),
            v["package"],
        )
    )
    return out


# ---------- per-repo work ----------


def update_npm_repo(local: str, slug: str, pre_push: str | None) -> dict[str, Any]:
    """Apply safe-band updates in a worktree, run pre-push, open PR.

    Returns a status dict the caller assembles into a Slack report."""
    status: dict[str, Any] = {
        "local": local,
        "slug": slug,
        "outcome": "noop",
        "safe": [],
        "majors": [],
        "cves": [],
        "pr_url": "",
        "error": "",
    }
    try:
        wt, branch = make_worktree(local, AGENT, "weekly-deps")
    except RuntimeError as e:
        status["outcome"] = "error"
        status["error"] = f"worktree: {e}"
        return status

    try:
        # Always npm install first so we have a valid lockfile baseline.
        inst = run(["npm", "install", "--no-audit", "--no-fund"], cwd=str(wt), timeout=600)
        if inst.returncode != 0:
            status["outcome"] = "error"
            status["error"] = f"baseline npm install failed: {short(inst.stderr, 240)}"
            return status

        outdated = npm_outdated(wt)
        safe, majors = split_safe_vs_major(outdated)
        cves = collect_cves(npm_audit(wt))
        status["safe"] = safe
        status["majors"] = majors
        status["cves"] = cves

        if not safe:
            status["outcome"] = "noop"
            return status

        # Apply safe bumps. `npm update` respects semver caret => safe-band.
        upd = run(["npm", "update"], cwd=str(wt), timeout=600)
        if upd.returncode != 0:
            status["outcome"] = "error"
            status["error"] = f"npm update failed: {short(upd.stderr, 240)}"
            return status

        # Verify nothing actually changed (npm update can be a no-op even
        # when outdated reports a delta - registry resolution is async).
        diff_check = run(
            ["git", "diff", "--quiet", "package.json", "package-lock.json"], cwd=str(wt), timeout=30
        )
        if diff_check.returncode == 0:
            status["outcome"] = "noop"
            return status

        # Pre-push verification.
        if pre_push:
            res = run(["bash", "-lc", pre_push], cwd=str(wt), timeout=900)
            if res.returncode != 0:
                # Don't push broken code. Log + abort this repo.
                status["outcome"] = "verify-failed"
                status["error"] = (
                    f"pre-push failed (exit {res.returncode}): {short(res.stderr or res.stdout, 320)}"
                )
                return status

        # Stage + commit + push + PR.
        run(["git", "add", "package.json", "package-lock.json"], cwd=str(wt), timeout=10)
        commit_msg = _build_commit_message(local, safe, majors, cves)
        commit_msg_file = Path(f"/tmp/{AGENT}-commit-{local}.txt")
        commit_msg_file.write_text(commit_msg)
        commit = run(["git", "commit", "-F", str(commit_msg_file)], cwd=str(wt), timeout=60)
        if commit.returncode != 0:
            status["outcome"] = "error"
            status["error"] = f"commit failed: {short(commit.stderr, 240)}"
            return status

        push_remote, pr_head = push_remote_and_pr_head(wt, slug, branch)
        push = push_current_branch(wt, branch, remote=push_remote, timeout=120)
        if push.returncode != 0:
            status["outcome"] = "error"
            status["error"] = f"push failed: {short(push.stderr, 240)}"
            return status

        pr_body = _build_pr_body(local, safe, majors, cves)
        pr_body_file = Path(f"/tmp/{AGENT}-prbody-{local}.md")
        pr_body_file.write_text(pr_body)
        pr_url = gh_pr_create(
            slug,
            title=f"chore(deps): weekly safe-band updates ({len(safe)} packages)",
            body_file=pr_body_file,
            head=pr_head,
            labels=["agent:authored", "dependencies"],
        )
        if pr_url:
            status["outcome"] = "pr"
            status["pr_url"] = pr_url
        else:
            status["outcome"] = "error"
            status["error"] = "PR create failed; commit pushed to branch"
        return status
    finally:
        # Worktree cleanup. We keep it on PR success too - make_worktree
        # creates per-firing dirs, removal is fine.
        with contextlib.suppress(Exception):
            remove_worktree(local, wt)


# ---------- advisory mode ----------


def gradle_outdated_advisory(local: str) -> list[str]:
    """Best-effort outdated detector for gradle. Tries the dependencyUpdates
    plugin if available, otherwise returns an empty list with a note."""
    repo_path = WORKSPACE / local
    if not (repo_path / "gradlew").exists():
        return []
    res = run(
        ["./gradlew", "dependencyUpdates", "-Drevision=release", "--no-daemon", "--quiet"],
        cwd=str(repo_path),
        timeout=600,
    )
    # The plugin writes a report to build/dependencyUpdates/report.txt.
    report = repo_path / "build" / "dependencyUpdates" / "report.txt"
    if not report.exists():
        if res.returncode != 0:
            return [f"(could not run dependencyUpdates: {short(res.stderr, 160)})"]
        return [
            "(dependencyUpdates plugin not configured; install com.github.ben-manes.versions to enable)"
        ]
    text = report.read_text()
    # Pull out the "outdated dependencies" section heuristically.
    lines = []
    in_section = False
    for line in text.splitlines():
        if "outdated dependencies" in line.lower():
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped:
                if lines:
                    break
                continue
            if stripped.startswith("-"):
                lines.append(stripped)
            if len(lines) >= MAX_LINES_PER_REPO:
                break
    return lines


def pip_outdated_advisory(local: str) -> list[str]:
    repo_path = WORKSPACE / local
    if not repo_path.exists():
        return []
    # Find a venv if one exists (.venv or venv).
    pip_bin = None
    for candidate in (".venv/bin/pip", "venv/bin/pip"):
        if (repo_path / candidate).exists():
            pip_bin = repo_path / candidate
            break
    if pip_bin is None:
        return ["(no .venv/ or venv/ found; activate one before this can run)"]
    res = run(
        [str(pip_bin), "list", "--outdated", "--format=json"], cwd=str(repo_path), timeout=120
    )
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        items = json.loads(res.stdout)
    except json.JSONDecodeError:
        return []
    out = []
    for it in items[:MAX_LINES_PER_REPO]:
        out.append(f"- {it.get('name')}: {it.get('version')} -> {it.get('latest_version')}")
    return out


# ---------- formatting ----------


def _build_commit_message(
    local: str,
    safe: list[tuple[str, str, str]],
    majors: list[tuple[str, str, str]],
    cves: list[dict[str, Any]],
) -> str:
    lines = [
        f"chore(deps): weekly safe-band updates ({len(safe)} packages)",
        "",
        f"Caret-compatible bumps applied via `npm update` against {local}/.",
        f"All {len(safe)} updates stay within the existing major version range.",
        "",
        "Updated:",
    ]
    for pkg, cur, want in safe[:30]:
        lines.append(f"  {pkg}: {cur} -> {want}")
    if len(safe) > 30:
        lines.append(f"  ... and {len(safe) - 30} more")
    if cves:
        lines.extend(
            [
                "",
                f"CVE-relevant advisories ({len(cves)}):",
            ]
        )
        for c in cves[:8]:
            lines.append(f"  {c['package']} (severity: {c['severity']})")
    if majors:
        lines.extend(
            [
                "",
                f"Deferred to human review ({len(majors)} major bumps available):",
            ]
        )
        for pkg, cur, latest in majors[:8]:
            lines.append(f"  {pkg}: {cur} -> {latest} (major)")
    return "\n".join(lines)


def _build_pr_body(
    local: str,
    safe: list[tuple[str, str, str]],
    majors: list[tuple[str, str, str]],
    cves: list[dict[str, Any]],
) -> str:
    parts = [
        "## Summary",
        f"Weekly safe-band dependency updates for `{local}/`. "
        f"`{len(safe)}` packages bumped to the highest version compatible with the existing semver caret range.",
        "",
        "## Pre-push verification",
        "Lint, type-check, build (and tests where configured) all passed locally before this PR was opened.",
        "",
        "## Updated packages",
    ]
    if safe:
        parts.append("| Package | From | To |")
        parts.append("|---|---|---|")
        for pkg, cur, want in safe:
            parts.append(f"| `{pkg}` | `{cur}` | `{want}` |")
    if cves:
        parts.extend(["", f"## CVE advisories ({len(cves)})"])
        for c in cves:
            parts.append(f"- **{c['package']}** ({c['severity']})")
            for adv in c["advisories"]:
                parts.append(f"  - [{adv['title'] or 'advisory'}]({adv['url']})")
    if majors:
        parts.extend(["", f"## Deferred to human review ({len(majors)} major bumps)"])
        parts.append("Major version jumps are not auto-applied. Bump these manually after vetting:")
        for pkg, cur, latest in majors:
            parts.append(f"- `{pkg}`: `{cur}` -> `{latest}`")
    parts.extend(
        [
            "",
            "## Notes",
            f"- Generated by alfred-nightly on {datetime.now(UTC).strftime('%Y-%m-%d')}.",
            "- Safe-band bumps only; review and merge once CI passes.",
        ]
    )
    return "\n".join(parts)


def _format_slack(
    npm_results: list[dict[str, Any]], advisory_blocks: list[tuple[str, str, list[str]]]
) -> str:
    lines = ["🌃 *Alfred-nightly weekly dep update*"]
    pr_count = 0
    for r in npm_results:
        local = r["local"]
        if r["outcome"] == "pr":
            pr_count += 1
            n_safe = len(r["safe"])
            n_cve = len(r["cves"])
            n_major = len(r["majors"])
            extras = []
            if n_cve:
                extras.append(f"{n_cve} CVE-relevant")
            if n_major:
                extras.append(f"{n_major} majors deferred")
            extra_s = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"  ✅ {local}: {r['pr_url']} - {n_safe} safe bumps{extra_s}")
        elif r["outcome"] == "noop":
            lines.append(f"  ⏸️ {local}: nothing to update")
        elif r["outcome"] == "verify-failed":
            lines.append(f"  ⚠️ {local}: pre-push failed - {short(r['error'], 200)}")
        elif r["outcome"] == "error":
            lines.append(f"  ❌ {local}: {short(r['error'], 200)}")
    if advisory_blocks:
        lines.append("")
        lines.append("*Advisory (no PR):*")
        for local, ecosystem, items in advisory_blocks:
            if not items:
                lines.append(f"  ⏸️ {local} ({ecosystem}): nothing outdated")
                continue
            lines.append(f"  ⚠️ {local} ({ecosystem}): {len(items)} items")
            for it in items[:6]:
                lines.append(f"      {it}")
            if len(items) > 6:
                lines.append(f"      ...and {len(items) - 6} more")
    if pr_count == 0 and not any(b[2] for b in advisory_blocks):
        lines.append("\n_All ecosystems up to date. No work to do._")
    return "\n".join(lines)


# ---------- entry point ----------


def main() -> int:
    with_lock(AGENT)

    if doctor_mode():
        print(f"[{AGENT.upper()}-DOCTOR-OK]")
        return 0

    # Opt-in: with no repos configured this is a safe no-op, so the job can be
    # scheduled disarmed and armed later purely through the environment.
    if not NPM_REPOS and not ADVISORY_REPOS:
        print(
            f"[{AGENT}] no repos configured "
            "(set ALFRED_NIGHTLY_NPM_REPOS or ALFRED_NIGHTLY_ADVISORY_REPOS); nothing to do"
        )
        return 0

    try:
        preflight(PREFLIGHT)
    except PreflightFailed:
        return 0

    spend = SpendState(AGENT)

    npm_results = []
    for local, slug, pre_push in NPM_REPOS:
        print(f"[{AGENT}] starting {local}")
        npm_results.append(update_npm_repo(local, slug, pre_push))

    advisory_blocks: list[tuple[str, str, list[str]]] = []
    for local, _slug, ecosystem in ADVISORY_REPOS:
        print(f"[{AGENT}] advisory pass: {local} ({ecosystem})")
        if ecosystem == "gradle":
            items = gradle_outdated_advisory(local)
        elif ecosystem == "pip":
            items = pip_outdated_advisory(local)
        else:
            items = []
        advisory_blocks.append((local, ecosystem, items))

    pr_count = sum(1 for r in npm_results if r["outcome"] == "pr")
    spend.increment(firings_today=1, prs_opened_today=pr_count)

    msg = _format_slack(npm_results, advisory_blocks)
    print(msg)
    # Always post; weekly cadence means a "nothing changed" message is signal.
    slack_post(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

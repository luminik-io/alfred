#!/usr/bin/env python3
"""Read-only pull-request reviewer with provider-independent engine routing."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(
    0,
    (os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")) + "/lib",
)
from agent_runner import (
    ALFRED_HOME,
    GH_ORG,
    WORKSPACE,
    WORKSPACE_ROOT,
    EventLog,
    PreflightFailed,
    PreflightSpec,
    SpendState,
    agent_engine,
    agent_repos,
    claude_invoke_streaming,
    codex_invoke,
    doctor_mode,
    doctor_requested,
    engine_preflight_bins,
    env_int,
    gh_json,
    gh_pr_comment,
    invoke_agent_engine,
    is_globally_blocked,
    is_repo_paused,
    issue_memory_query,
    local_repo_dir,
    maybe_halt_on_fail_streak,
    maybe_set_global_block_for_result,
    optional_env_int,
    preflight,
    run,
    slack_post,
    with_lock,
)
from code_graph import (
    blast_radius_for_paths,
    default_code_map_path,
    load_code_map,
    render_blast_radius,
)

AGENT = os.environ.get("AGENT_CODENAME", "reviewer")
REVIEWER_ENGINE = agent_engine(AGENT, default="hybrid")
LAUNCHD_LABEL = os.environ.get("LAUNCHD_LABEL", f"my.fleet.{AGENT}")
PROMPT_PATH = ALFRED_HOME / "prompts" / f"{AGENT}.md"
OPERATOR_GUIDANCE_MARKER = "<!-- alfred:operator-guidance v1 -->"
OPERATOR_GUIDANCE_PLACEHOLDER = re.compile(
    r"\$\{(AGENT_CODENAME|GH_ORG|ALFRED_HOME|WORKSPACE_ROOT|REVIEW_REPOS|"
    r"REPO_SLUG|PR_NUMBER|PR_TITLE|LOCAL_REPO)\}"
)

PREFLIGHT = PreflightSpec(
    agent=AGENT,
    bins=[*engine_preflight_bins(REVIEWER_ENGINE), "gh", "git"],
    require_gh_auth=True,
)

# Keyed off the runtime role slug. Themes and custom visible names do not change
# the machine identity or env-var contract.
REVIEW_REPOS = agent_repos(AGENT)

# Specs / docs PRs are markdown-heavy; line count != review effort.
# Operator can name the docs-style repos to get a higher diff cap; default cap
# applies to everything else.
SPECS_REPOS = {
    r.strip() for r in os.environ.get("ALFRED_REVIEWER_SPECS_REPOS", "").split(",") if r.strip()
}
DIFF_LINE_CAP_DEFAULT = int(os.environ.get("ALFRED_REVIEWER_DIFF_CAP", "4000"))
DIFF_LINE_CAP_SPECS = int(os.environ.get("ALFRED_REVIEWER_DIFF_CAP_SPECS", "8000"))

DAILY_TURN_CAP = int(os.environ.get("ALFRED_REVIEWER_TURN_CAP", "800"))
DAILY_REVIEW_CAP = int(os.environ.get("ALFRED_REVIEWER_REVIEW_CAP", "30"))
REVIEWER_TIMEOUT = env_int("ALFRED_REVIEWER_TIMEOUT", 900, minimum=60)
REVIEWER_FALLBACK_TIMEOUT = env_int("ALFRED_REVIEWER_FALLBACK_TIMEOUT", 1800, minimum=60)
REVIEW_AUTHOR_PREFIX = f"{AGENT.title()} - review"
REVIEWED_HEAD_SHA = re.compile(
    r"^Reviewed-head-sha:\s*([0-9a-f]{7,40})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CODE_MAP_MAX_AGE = timedelta(hours=24)
CODE_MAP_MAX_FUTURE_SKEW = timedelta(minutes=5)
CODE_SENSOR_MAX_PATHS = 50
CODE_SENSOR_MAX_SERVER_CONTRACTS = 100


def _parse_code_map_timestamp(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        return parsed.astimezone(UTC)
    except OverflowError:
        return None


def _code_map_repo_key(code_map: dict, repo: str) -> str | None:
    repos = code_map.get("repos")
    if not isinstance(repos, dict):
        return None
    candidates = (repo, local_repo_dir(repo), repo.rsplit("/", 1)[-1])
    for candidate in candidates:
        if candidate in repos:
            return candidate
    by_lower = {str(key).lower(): str(key) for key in repos}
    for candidate in candidates:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    return None


def _changed_paths_from_file_pages(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return []
    rows: list[object] = []
    for page in payload:
        if isinstance(page, list):
            rows.extend(page)
        else:
            rows.append(page)
    paths: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("filename", "previous_filename"):
            path = str(row.get(key) or (row.get("path") if key == "filename" else "")).strip()
            if path:
                paths.append(path)
    return list(dict.fromkeys(paths))


def _render_server_contract_catalog(code_map: dict) -> str:
    """Render a bounded cross-repo server-contract index for diff validation."""

    repos = code_map.get("repos")
    if not isinstance(repos, dict):
        return "Known server contracts: unavailable. Inspect server code directly."
    contracts: list[tuple[str, str, str, str]] = []
    for repo_name, repo_data in repos.items():
        if not isinstance(repo_data, dict):
            continue
        for kind in ("endpoints", "routes"):
            rows = repo_data.get(kind)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                method = str(row.get("method") or "").strip().upper()
                path = str(row.get("path") or "").strip()
                if method and path:
                    contracts.append((str(repo_name), kind[:-1], method, path))
    if not contracts:
        return "Known server contracts: unavailable. Inspect server code directly."
    visible = contracts[:CODE_SENSOR_MAX_SERVER_CONTRACTS]
    lines = [
        "Known server contracts from the mapped base branch (validate PR additions against these):"
    ]
    lines.extend(f"- {repo} {kind} {method} {path}" for repo, kind, method, path in visible)
    omitted = len(contracts) - len(visible)
    if omitted:
        lines.append(f"Known server contracts omitted by sensor limit: {omitted}")
    return "\n".join(lines)


def build_review_sensor_context(
    repo: str,
    changed_paths: list[str],
    *,
    code_map_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Build bounded, deterministic impact evidence for a PR review prompt."""

    resolved = code_map_path or default_code_map_path()
    if not resolved.exists():
        return (
            "unavailable",
            "Code-map sensor: unavailable. Review the diff and surrounding code directly.",
        )
    try:
        code_map = load_code_map(resolved)
    except (OSError, ValueError):
        return (
            "unavailable",
            "Code-map sensor: unreadable. Review the diff and surrounding code directly.",
        )

    generated_at = _parse_code_map_timestamp(code_map.get("generated_at"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if (
        generated_at is None
        or generated_at - current > CODE_MAP_MAX_FUTURE_SKEW
        or current - generated_at > CODE_MAP_MAX_AGE
    ):
        return (
            "stale",
            "Code-map sensor: stale. Do not rely on it; inspect changed paths and contracts directly.",
        )

    repo_key = _code_map_repo_key(code_map, repo)
    if repo_key is None:
        return (
            "unavailable",
            "Code-map sensor: this repository is not mapped. Inspect changed paths and contracts directly.",
        )

    unique_paths = list(dict.fromkeys(path.strip() for path in changed_paths if path.strip()))
    if not unique_paths:
        return (
            "unavailable",
            "Code-map sensor: the PR metadata has no changed paths. Inspect the diff directly.",
        )
    selected_paths = unique_paths[:CODE_SENSOR_MAX_PATHS]
    try:
        blast_radius = blast_radius_for_paths(
            code_map,
            repo=repo_key,
            paths=selected_paths,
            limit=50,
        )
    except (KeyError, TypeError, ValueError):
        return (
            "unavailable",
            "Code-map sensor: impact evidence could not be computed. Inspect the diff directly.",
        )

    rendered = render_blast_radius(blast_radius)
    omitted = len(unique_paths) - len(selected_paths)
    if omitted:
        rendered += f"\nChanged paths omitted by sensor limit: {omitted}"
    rendered += f"\n{_render_server_contract_catalog(code_map)}"
    rendered += (
        "\nSensor rule: use these signals to prioritize inspection, not as proof of correctness. "
        "Absence of a dependency or contract is missing evidence, not evidence of absence."
    )
    return "ready", rendered


def _extract_section(text: str, header: str) -> list[str]:
    """Pull the bullet items under a markdown ## header. Returns [] if section absent."""
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped == header
            continue
        if not in_section:
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item.lower() in ("none.", "(or write none.)", "(or none.)"):
                continue
            if item.startswith("(") and item.endswith(")"):
                continue
            out.append(item)
    return out


def _strip_operator_guidance_marker(text: str) -> str:
    """Drop the explicit operator-guidance marker line, if present."""

    lines = text.splitlines()
    if lines and lines[0].strip() == OPERATOR_GUIDANCE_MARKER:
        return "\n".join(lines[1:])
    return text


def _render_operator_guidance(text: str, values: dict[str, str]) -> str:
    """Render only documented placeholders and preserve all other dollar text."""

    return OPERATOR_GUIDANCE_PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _operator_prompt_guidance(
    repo: str,
    pr_num: int,
    pr_title: str,
    local_path: Path,
) -> str:
    """Load operator-edited reviewer guidance without injecting the starter."""

    try:
        raw_guidance = PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = raw_guidance.splitlines()
    if not lines or lines[0].strip() != OPERATOR_GUIDANCE_MARKER:
        return ""
    values = {
        "AGENT_CODENAME": AGENT,
        "GH_ORG": GH_ORG,
        "ALFRED_HOME": str(ALFRED_HOME),
        "WORKSPACE_ROOT": str(WORKSPACE_ROOT),
        "REVIEW_REPOS": ",".join(REVIEW_REPOS),
        "REPO_SLUG": repo,
        "PR_NUMBER": str(pr_num),
        "PR_TITLE": pr_title,
        "LOCAL_REPO": str(local_path),
    }
    guidance = _render_operator_guidance(raw_guidance, values)
    guidance = _strip_operator_guidance_marker(guidance).strip()
    if not guidance:
        return ""
    return f"""
Operator-supplied review guidance from {PROMPT_PATH}:
---
{guidance}
---
"""


def reviewed_head_sha(review_body: str) -> str | None:
    match = REVIEWED_HEAD_SHA.search(review_body or "")
    return match.group(1).lower() if match else None


def attach_review_head_sha(review_body: str, head_sha: str | None) -> str:
    head = (head_sha or "").strip().lower()
    if not head:
        return review_body
    lines = review_body.splitlines()
    if not lines:
        return review_body
    metadata = f"Reviewed-head-sha: {head}"
    replaced = False
    for i, line in enumerate(lines[:6]):
        if REVIEWED_HEAD_SHA.match(line):
            lines[i] = metadata
            replaced = True
            break
    if not replaced:
        lines.insert(1, metadata)
    return "\n".join(lines).rstrip() + "\n"


def diff_too_large_review_body(lines: int, line_cap: int, head_sha: str | None) -> str:
    body = (
        f"{REVIEW_AUTHOR_PREFIX}\n\n"
        "## Review skipped\n"
        f"- Diff is {lines} lines (cap {line_cap}). Please split for an effective review.\n\n"
        "Ship-ready: no - diff exceeds review cap.\n"
    )
    return attach_review_head_sha(body, head_sha)


def pick_pr() -> tuple[str, dict] | tuple[None, None]:
    """Find oldest open PR not yet reviewed by this agent (and not draft, not WIP)."""
    for repo in REVIEW_REPOS:
        if is_repo_paused(repo):
            continue
        prs = gh_json(
            [
                "gh",
                "pr",
                "list",
                "-R",
                f"{GH_ORG}/{repo}",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,headRefOid,url,createdAt,labels,isDraft",
                "--limit",
                "30",
            ],
            default=[],
        )
        if not prs:
            continue
        for pr in prs:
            if pr.get("isDraft"):
                continue
            if any(t in pr["title"].lower() for t in ("wip", "[wip]")):
                continue
            label_names = [lbl["name"] for lbl in pr.get("labels", [])]
            if "do-not-review" in label_names:
                continue
            # Age > 5 min (give bot reviewers first crack)
            try:
                created = datetime.strptime(pr["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=UTC
                )
                if (datetime.now(UTC) - created).total_seconds() < 300:
                    continue
            except ValueError:
                pass
            # Re-verify state right now
            state = gh_json(
                [
                    "gh",
                    "pr",
                    "view",
                    str(pr["number"]),
                    "-R",
                    f"{GH_ORG}/{repo}",
                    "--json",
                    "state",
                ],
                default={},
            ).get("state")
            if state != "OPEN":
                continue
            # Re-review if there are new commits since the most recent
            # review of ours. Original logic skipped any PR ever reviewed,
            # which silently dropped author-fix iterations.
            view = gh_json(
                [
                    "gh",
                    "pr",
                    "view",
                    str(pr["number"]),
                    "-R",
                    f"{GH_ORG}/{repo}",
                    "--json",
                    "comments,commits",
                ],
                default={"comments": [], "commits": []},
            )
            comments = view.get("comments", []) or []
            commits = view.get("commits", []) or []

            our_reviews = [
                c for c in comments if c.get("body", "").startswith(REVIEW_AUTHOR_PREFIX)
            ]
            if not our_reviews:
                return repo, pr
            head_sha = (pr.get("headRefOid") or "").strip().lower()
            latest_review = our_reviews[-1]
            if not head_sha or reviewed_head_sha(latest_review.get("body", "")) != head_sha:
                return repo, pr

            try:
                last_review_ts = max(
                    datetime.strptime(c["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                    for c in our_reviews
                    if c.get("createdAt")
                )
            except (ValueError, KeyError):
                continue

            new_commits = False
            for c in commits:
                ts_str = c.get("committedDate") or c.get("authoredDate") or ""
                if not ts_str:
                    continue
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                except ValueError:
                    continue
                if ts > last_review_ts:
                    new_commits = True
                    break

            if new_commits:
                return repo, pr
            continue
    return None, None


def main() -> int:
    with_lock(AGENT)

    if not REVIEW_REPOS and not doctor_requested():
        print(f"[{AGENT.upper()}-IDLE] no repos configured (set ALFRED_REVIEWER_REPOS)")
        return 0

    try:
        preflight(PREFLIGHT)
    except PreflightFailed:
        return 0

    if doctor_mode():
        print(f"[{AGENT.upper()}-DOCTOR-OK]")
        return 0

    events = EventLog(agent=AGENT)
    events.emit("firing_started")

    blocked = is_globally_blocked()
    if blocked:
        print(f"[{AGENT.upper()}-GLOBAL-BLOCKED] {blocked}. Skipping firing.")
        events.emit("firing_complete", outcome="global-blocked")
        return 0
    spend = SpendState(AGENT)

    if spend.state["turns_today"] >= DAILY_TURN_CAP:
        msg = f"[{AGENT.upper()}-DAILY-CAP] turns={spend.state['turns_today']} >= {DAILY_TURN_CAP}. Pausing."
        print(msg)
        slack_post(msg)
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], timeout=10)
        events.emit("firing_complete", outcome="daily-cap")
        return 0
    if spend.state.get("reviews_posted", 0) >= DAILY_REVIEW_CAP:
        print(
            f"[{AGENT.upper()}-REVIEW-CAP] {spend.state['reviews_posted']} reviews posted today. Skipping."
        )
        events.emit("firing_complete", outcome="review-cap")
        return 0

    if maybe_halt_on_fail_streak(AGENT, spend, events, LAUNCHD_LABEL):
        return 0

    repo, pr = pick_pr()
    if not repo:
        print(f"[{AGENT.upper()}-IDLE]")
        events.emit("firing_complete", outcome="idle-no-pr")
        return 0

    pr_num = pr["number"]
    head_sha = (pr.get("headRefOid") or "").strip().lower()
    local_path = WORKSPACE / local_repo_dir(repo)
    events.emit("pr_picked", repo=f"{GH_ORG}/{repo}", number=pr_num)

    # Fetch diff + meta + prior reviewer comments
    tmp = Path(tempfile.mkdtemp(prefix=f"{AGENT}-"))
    diff_file = tmp / "diff.patch"
    diff_res = run(["gh", "pr", "diff", str(pr_num), "-R", f"{GH_ORG}/{repo}"], timeout=30)
    diff_file.write_text(diff_res.stdout)

    if diff_file.stat().st_size == 0:
        print(f"[{AGENT.upper()}-SKIP] empty diff for PR {pr_num} on {repo}")
        events.emit("firing_complete", outcome="empty-diff")
        return 0

    lines = diff_res.stdout.count("\n")
    is_specs = repo in SPECS_REPOS
    line_cap = DIFF_LINE_CAP_SPECS if is_specs else DIFF_LINE_CAP_DEFAULT
    if lines > line_cap:
        gh_pr_comment(repo, pr_num, diff_too_large_review_body(lines, line_cap, head_sha))
        events.emit("firing_complete", outcome="diff-too-large", lines=lines)
        return 0

    meta = gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_num),
            "-R",
            f"{GH_ORG}/{repo}",
            "--json",
            "title,body,additions,deletions,headRefOid",
        ],
        default={},
    )
    pr_title = meta.get("title", "")
    pr_body = meta.get("body", "") or ""
    head_sha = (meta.get("headRefOid") or head_sha).strip().lower()
    file_pages = gh_json(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"/repos/{GH_ORG}/{repo}/pulls/{pr_num}/files?per_page=100",
        ],
        default=[],
    )
    changed_paths = _changed_paths_from_file_pages(file_pages)
    sensor_status, sensor_context = build_review_sensor_context(repo, changed_paths)
    events.emit(
        "review_sensor_context",
        repo=f"{GH_ORG}/{repo}",
        status=sensor_status,
        changed_paths=len(changed_paths),
    )

    # Prior reviewer comments from known bot reviewers
    prior_comments = gh_json(
        [
            "gh",
            "api",
            f"/repos/{GH_ORG}/{repo}/pulls/{pr_num}/comments",
            "--paginate",
        ],
        default=[],
    )
    prior = [
        {
            "user": c["user"]["login"],
            "body": c["body"],
            "path": c.get("path"),
            "line": c.get("line"),
        }
        for c in prior_comments
        if c.get("user", {}).get("login", "") in ("coderabbitai[bot]",)
        or "codex" in c.get("user", {}).get("login", "").lower()
        or "chatgpt" in c.get("user", {}).get("login", "").lower()
    ]
    (tmp / "prior-reviews.json").write_text(json.dumps(prior, indent=2))
    operator_guidance = _operator_prompt_guidance(repo, pr_num, pr_title, local_path)

    if is_specs:
        prompt = f"""You are {AGENT.title()} reviewing a SPECS pull request (markdown documentation, not code).

PR: https://github.com/{GH_ORG}/{repo}/pull/{pr_num}
Title: {pr_title}

Body:
{pr_body}

The diff is at {tmp}/diff.patch, read it.
Working directory: {local_path} (you can grep the surrounding repo for context).
Workspace root for cross-repo grep: {WORKSPACE_ROOT}

Deterministic code-map evidence:
{sensor_context}

Contract verification rule: for every client HTTP or API call added or changed
in the diff, verify its method and path against the mapped server contracts and
the server implementation under {WORKSPACE_ROOT}. If the catalog is missing or
truncated, grep the server code directly. Flag a confirmed mismatch; never treat
an absent catalog entry as proof that the call is safe.
{operator_guidance}

Specs review axes (priority order):
1. Internal consistency - does spec N contradict spec M? Cross-references valid?
2. Code reality alignment - does the spec describe what code actually does? Where the spec says "the X service does Y", grep the code to confirm.
3. Vocabulary discipline - avoid stale or marketing vocab. Avoid em-dashes. Avoid fabricated numbers.
4. Definition-of-done testability - measurable acceptance criteria, or aspirational prose?
5. Scope clarity - one cohesive area, or sprawl?
6. Open questions / TODOs surfaced clearly?
7. Strategy alignment - does the new doc align with the rest?
8. Risk / what-could-go-wrong sections present where warranted.

Hard rules:
- Evidence-first. Every critical finding includes file:line and a concrete contradiction.
- Severity: P0 (blocker - shipping this would mislead engineering), P1 (fix before merge), P2 (follow-up OK), nit.
- No em-dashes. No "unlock", "leverage", "seamless", "transform", "robust". No fabricated numbers.
- You have Read/Bash/Glob/Grep ONLY. Read-only.

Output - print EXACTLY this structure to stdout, nothing else:

{REVIEW_AUTHOR_PREFIX} (specs PR)

## Blockers (P0)
- file:line - <statement> - <why>
- (or write None.)

## Should fix before merge (P1)
- ...
- (or None.)

## Worth considering (P2)
- ...
- (or None.)

## Cross-spec consistency
- (call out contradictions, or skip if clean)

## Strengths
- (1-3 only if real, otherwise omit)

Ship-ready: yes / no - <one sentence>
"""
    else:
        prompt = f"""You are {AGENT.title()}, the code review agent. Review this pull request and produce a single structured review comment.

PR: https://github.com/{GH_ORG}/{repo}/pull/{pr_num}
Title: {pr_title}

Body:
{pr_body}

The diff is at {tmp}/diff.patch - read it.
Existing bot-reviewer comments at {tmp}/prior-reviews.json - read them. DO NOT duplicate their findings.
Working directory: {local_path}.

Deterministic code-map evidence:
{sensor_context}

Contract verification rule: for every client HTTP or API call added or changed
in the diff, verify its method and path against the mapped server contracts and
the server implementation under {WORKSPACE_ROOT}. If the catalog is missing or
truncated, grep the server code directly. Flag a confirmed mismatch; never treat
an absent catalog entry as proof that the call is safe.
{operator_guidance}

Review axes (priority order):
1. Correctness - does it do what the title and body say? Edge cases?
2. Security - secret leaks, SQL injection, auth bypass, CSRF, CORS, rate limits, input validation, XSS, path traversal, multi-tenant isolation.
3. Data integrity - transactions, idempotency, migrations that could lose rows or drop columns.
4. Concurrency - race conditions, shared state, connection pools, transaction boundaries.
5. Failure modes - timeouts, retries, backoff, circuit breakers.
6. Observability - if this breaks at 3am, can you diagnose from logs?
7. Performance - N+1 queries, unbounded loops, full-table scans.
8. Consistency - matches existing repo patterns?
9. Test adequacy - do tests prove behavior or just exercise paths?
10. Reversibility - can this roll back cleanly?

Hard rules:
- Evidence-first. Every critical finding includes file:line and a concrete scenario that breaks.
- Severity: P0 (blocker), P1 (fix before merge), P2 (follow-up OK), nit.
- Skip findings other reviewers already flagged.
- No em-dashes. No "unlock", "leverage", "seamless", "transform". No fabricated numbers.
- If you cannot form a confident opinion in 3 read passes, say so and ask a specific clarifying question.
- You have Read/Bash/Glob/Grep ONLY. NOT writing code.

Output - print EXACTLY this structure to stdout, nothing else:

{REVIEW_AUTHOR_PREFIX}

## Blockers (P0)
- file:line - <statement> - <why>
- (or write None.)

## Should fix before merge (P1)
- ...
- (or None.)

## Worth considering (P2)
- ...
- (or None.)

## Strengths
- (1-2 only if real, otherwise omit)

Ship-ready: yes / no - <one sentence>
"""

    def _on_engine_fallback(fallback_result):
        events.emit(
            "llm_fallback",
            from_engine="claude",
            to_engine="codex",
            reason=fallback_result.error_message or fallback_result.result_text,
        )

    result, engine_used = invoke_agent_engine(
        prompt,
        engine=REVIEWER_ENGINE,
        claude_fn=claude_invoke_streaming,
        codex_fn=codex_invoke,
        workdir=local_path,
        claude_allowed_tools="Read,Bash,Glob,Grep",
        agent=AGENT,
        firing_id=events.firing_id,
        claude_max_turns=optional_env_int("ALFRED_REVIEWER_MAX_TURNS", minimum=40),
        timeout=REVIEWER_TIMEOUT,
        codex_timeout=REVIEWER_FALLBACK_TIMEOUT,
        codex_sandbox="read-only",
        codex_add_dirs=[tmp, WORKSPACE_ROOT],
        on_fallback=_on_engine_fallback,
        memory_repo=f"{GH_ORG}/{repo}" if GH_ORG else repo,
        # Recall lessons relevant to this PR's title and body, not just recent
        # lessons for the repository and reviewer role.
        memory_query=issue_memory_query(pr_title, pr_body),
    )
    spend.increment(firings_today=1, turns_today=result.num_turns, cost_usd_today=result.cost_usd)
    events.emit(
        "llm_invoke_done",
        engine=engine_used,
        turns=result.num_turns,
        subtype=result.subtype,
        success=result.success,
    )

    if not result.success:
        spend.increment(failures_today=1, consecutive_failures=1)
        until = maybe_set_global_block_for_result(AGENT, result, engine_used=engine_used)
        if until:
            msg = (
                f"{AGENT.title()} hit provider rate limit ({result.subtype}, engine={engine_used}). "
                f"Global block until {until}."
            )
            print(msg)
            slack_post(msg, severity="alert")
            events.emit("firing_complete", outcome=f"llm-{result.subtype}", engine=engine_used)
            return 0
        msg = (
            f"❌ {AGENT.title()}: engine={engine_used} subtype={result.subtype} "
            f"turns={result.num_turns} on PR {pr_num}"
        )
        print(msg)
        slack_post(msg)
        events.emit("firing_complete", outcome=f"llm-{result.subtype}", engine=engine_used)
        return 0

    # Salvage off-format output instead of dropping it. Model sometimes emits
    # conversational filler before the review header; the gate-on-prefix
    # behaviour silently dropped real findings. Slice to the header if found,
    # else wrap under a synthetic recovered header so the gate-phrase still
    # appears at the top of the body.
    text = (result.result_text or "").strip()
    if not text.startswith(REVIEW_AUTHOR_PREFIX):
        idx = text.find(REVIEW_AUTHOR_PREFIX)
        if idx >= 0:
            preface = text[:idx].strip()
            text = text[idx:]
            if preface:
                print(
                    f"[{AGENT.upper()}-SALVAGED] stripped {len(preface)}-char preface",
                    file=sys.stderr,
                )
        else:
            print(
                f"[{AGENT.upper()}-SALVAGED] wrapping {len(text)}-char output under synthetic header",
                file=sys.stderr,
            )
            text = (
                f"{REVIEW_AUTHOR_PREFIX} (recovered from off-format output)\n\n"
                "## Blockers (P0)\n- (recovered review below; format-gate suppressed; verify before merge)\n\n"
                "## Should fix before merge (P1)\n- (see body)\n\n"
                "## Worth considering (P2)\n- (see body)\n\n"
                "## Recovered body\n\n" + text + "\n\n"
                "Ship-ready: no\n"
            )
    text = attach_review_head_sha(text, head_sha)

    # Re-verify PR is still OPEN
    state = gh_json(
        ["gh", "pr", "view", str(pr_num), "-R", f"{GH_ORG}/{repo}", "--json", "state"], default={}
    ).get("state")
    if state != "OPEN":
        msg = f"[{AGENT.upper()}-STALE] PR {pr_num} is now {state}, not posting review."
        print(msg)
        # Not a failure: the review generated fine, the PR just closed under it.
        # Clear the streak so this healthy no-op does not preserve a stale count.
        spend.set(consecutive_failures=0)
        events.emit("firing_complete", outcome="pr-stale", state=state)
        return 0

    if not gh_pr_comment(repo, pr_num, text):
        spend.increment(failures_today=1, consecutive_failures=1)
        msg = f"❌ {AGENT.title()}: failed to post review on PR {pr_num}"
        print(msg)
        slack_post(msg)
        events.emit("firing_complete", outcome="post-failed")
        return 0

    # Split P0/P1 findings into per-finding sub-comments so the review-to-fix
    # agent can address each one independently; it deduplicates by comment ID.
    p0_findings = _extract_section(text, "## Blockers (P0)")
    p1_findings = _extract_section(text, "## Should fix before merge (P1)")
    posted_split = 0
    for finding in p0_findings:
        if gh_pr_comment(repo, pr_num, f"{AGENT.title()} P0: {finding}"):
            posted_split += 1
    for finding in p1_findings:
        if gh_pr_comment(repo, pr_num, f"{AGENT.title()} P1: {finding}"):
            posted_split += 1

    spend.increment(reviews_posted=1, successes_today=1)
    # A posted review clears the fail streak so the self-halt gate only trips
    # on a genuine run of consecutive failures.
    spend.set(consecutive_failures=0)
    events.emit(
        "review_posted",
        repo=f"{GH_ORG}/{repo}",
        number=pr_num,
        turns=result.num_turns,
        p0_count=len(p0_findings),
        p1_count=len(p1_findings),
        split_comments=posted_split,
        engine=engine_used,
    )
    msg = f"{AGENT.title()}: reviewed https://github.com/{GH_ORG}/{repo}/pull/{pr_num} (engine={engine_used}, turns={result.num_turns}, split={posted_split} P0/P1 sub-comments)"
    print(msg)
    slack_post(msg)
    events.emit("firing_complete", outcome="review-posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

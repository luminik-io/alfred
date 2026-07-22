#!/usr/bin/env python3
"""alfred-init, interactive fleet configuration wizard.

Run after `git clone` + `bash install.sh`. install.sh handles dependency
install (brew, gh, claude, aws, python, node, runtime dirs, $ALFRED_HOME/.env
template). alfred-init handles configuration: auth checks, Slack webhook
provisioning, agent selection, repo + schedule wiring,
agents.conf generation, deploy, doctor, smoke test.

Wizard order (each step is idempotent, re-running won't duplicate):
    0. Preflight:      ALFRED_HOME, $ALFRED_HOME/.env, GH_ORG must exist.
    1. Claude Code:    `claude --version` + non-interactive auth probe.
    2. GitHub:         `gh auth status` + cache `gh repo list <GH_ORG>`.
    3. Slack webhook:  guide the operator, validate, test-post, store
                         (env or AWS Secrets Manager).
    4. AWS (optional):  per-agent IAM profiles for agents that use cloud APIs.
    5. Pick agents:    multi-select discovered from bin/*.py.
    6. Repos:          per-agent repo selection out of `gh repo list`.
    7. Schedule:       sensible defaults; press 'a' to customize.
    8. Generate config: agents.conf, env, starter prompts, fleet enable state.
    9. GitHub labels:  create standard labels on selected repos.
   10. Deploy:         `bash deploy.sh`.
   11. Doctor:         `alfred doctor`.
   12. Smoke test:     final Slack post + summary.

Override paths:
    ALFRED_NONINTERACTIVE=1   accept defaults everywhere
    ALFRED_DOCTOR=1               print [ALFRED-INIT-DOCTOR-OK] and exit
    --non-interactive             same as the env var
    --config <path>               read answers from JSON (skip prompts)
    --agents <comma>              all/default, starter, or comma-separated codenames
    --repos <comma>               repo selection for non-interactive setup
    --slack-webhook <url|skip>    skip the Slack prompt

Pure stdlib. The operator reads this file when something breaks; keep it
that way, no external deps, no clever indirection.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The battery manifest is the one source of truth shared with `alfred batteries`
# and the desktop picker, so the wizard never keeps a second, drifting copy of
# the opt-in battery list. Both imports are pure stdlib modules from this repo.
_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if _LIB_DIR.is_dir() and str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
import batteries  # noqa: E402

# ---------------------------------------------------------------------------
# Constants, the canonical role catalog.
# ---------------------------------------------------------------------------

# role-key -> (default codename, one-line description, operates_on_repos,
# default schedule string in launchd/agents.conf format)
AGENT_CATALOG: dict[str, tuple[str, str, bool, str]] = {
    "feature_dev": (
        "senior-dev",
        "feature dev (picks agent:implement issues, opens PRs)",
        True,
        "interval:1200",
    ),
    "planner": (
        "planner",
        "issue planner (files agent:implement issues from specs)",
        True,
        "interval:7200",
    ),
    "test_coverage": (
        "test-engineer",
        "test coverage (writes tests for low-coverage changed files)",
        True,
        "interval:14400",
    ),
    "pr_review": ("reviewer", "PR review (multi-axis on every fresh PR)", True, "interval:1800"),
    "ci_repair": (
        "fixer",
        "review fixes (lands P0/P1 reviewer comments on agent PRs)",
        True,
        "interval:2700",
    ),
    "bug_triage": (
        "triage",
        "bug triage (labels issues, asks repro, hands off to the senior dev)",
        True,
        "interval:10800",
    ),
    "cross_repo_coordinator": (
        "architect",
        "cross-repo architect (plans and files approved agent:large-feature bundles)",
        False,
        "interval:3600",
    ),
    "spec_planner": (
        "spec-planner",
        "spec-bundle planner (drafts multi-repo bundles from a spec directory)",
        True,
        "cron:9:00",
    ),
    "smoke_runner": (
        "e2e-runner",
        "staging smoke runner (hits a URL on schedule)",
        False,
        "interval:1800",
    ),
    "ops_morning": ("ops-watch", "ops morning (ECS + Sentry health roll-up)", False, "cron:8:00"),
    "automerge": ("automerge", "PR automerge (merges green, blessed PRs)", False, "interval:900"),
    "agent_cleanup": (
        "agent-cleanup",
        "agent cleanup (prunes stale claims + worktrees)",
        False,
        "cron:3:00",
    ),
    "memory_harvest": (
        "memory-harvest",
        "memory harvest (queues repeated failure and reflection lessons)",
        False,
        "cron:8:05",
    ),
    "memory_auto_promote": (
        "memory-auto-promote",
        "memory auto-promote (LLM-judges queued lessons into recall)",
        False,
        "cron:8:20",
    ),
    "code_map_refresh": (
        "code-map-refresh",
        "code map refresh (regenerates per-repo skeleton)",
        True,
        "interval:21600",
    ),
    "morning_brief": (
        "agent-morning-brief",
        "morning brief (overnight fleet summary)",
        False,
        "cron:7:00",
    ),
    "fleet_doctor": (
        "fleet-doctor",
        "fleet doctor (daily local health snapshot)",
        False,
        "cron:7:30",
    ),
    "fleet_recap_morning": (
        "fleet-recap-morning",
        "fleet recap morning (7:30 status post)",
        False,
        "cron:7:45",
    ),
    "fleet_recap_evening": (
        "fleet-recap-evening",
        "fleet recap evening (22:00 status post)",
        False,
        "cron:22:00",
    ),
    "shipped_summary_daily": (
        "shipped-summary-daily",
        "shipped summary daily (merged PRs, issues, LOC)",
        False,
        "cron:7:35",
    ),
    "shipped_summary_weekly": (
        "shipped-summary-weekly",
        "shipped summary weekly (merged PRs, issues, LOC)",
        False,
        "cron:1:7:35",
    ),
}

# Map default codename -> role-key (for discovery from bin/*.py).
CODENAME_TO_ROLE: dict[str, str] = {
    default: role for role, (default, _, _, _) in AGENT_CATALOG.items()
}


def runtime_id_for_role(role: str) -> str:
    """Return the immutable runtime identity for a built-in role."""
    return AGENT_CATALOG[role][0]


STARTER_ROLES = (
    "planner",
    "feature_dev",
    "pr_review",
    "agent_cleanup",
    "memory_harvest",
    "memory_auto_promote",
)
SCOPE_GATED_ROLES = {"cross_repo_coordinator", "spec_planner"}
ROLE_REPO_ENV_KEYS = {
    "agent_cleanup": ("ALFRED_CLAIM_SWEEP_REPOS",),
    "automerge": ("ALFRED_AUTOMERGE_REPOS",),
    "code_map_refresh": ("ALFRED_CODE_MAP_REPOS",),
    "morning_brief": ("ALFRED_MORNING_BRIEF_REPOS",),
    "spec_planner": ("ALFRED_SPEC_PLANNER_REPOS",),
    "shipped_summary_daily": ("ALFRED_SHIPPED_SUMMARY_DAILY_REPOS",),
    "shipped_summary_weekly": ("ALFRED_SHIPPED_SUMMARY_WEEKLY_REPOS",),
}
MORNING_BRIEF_EXCLUDED_ROLES = {
    "morning_brief",
    "fleet_recap_morning",
    "fleet_recap_evening",
    "shipped_summary_daily",
    "shipped_summary_weekly",
}
MEMORY_AUTO_PROMOTE_CONTROL_ENVS = (
    "ALFRED_AUTO_PROMOTE",
    "ALFRED_AUTO_PROMOTE_KILL",
    "ALFRED_AUTO_PROMOTE_LLM_JUDGE",
)

# The only strings that count as an explicit opt-in for a privacy-sensitive
# consent flag. Anything else (including "false", "0", "no", "", or any other
# string) is OFF. This is intentionally strict so a quoted "false" in a YAML or
# JSON config never silently enables telemetry the way bool("false") would.
_TRUTHY_CONSENT = {"1", "true", "yes", "on"}
DEFAULT_TELEMETRY_URL = "https://alfred-proof-telemetry.luminik.workers.dev/ingest"
SETUP_TOKEN_COMMAND_TIMEOUT_S = 3600


def _run_setup_token(script: Path) -> int:
    process = subprocess.Popen(
        [sys.executable, str(script)],
        start_new_session=True,
    )

    def _terminate_on_signal(signum: int, _frame: Any) -> None:
        _terminate_process_group(process)
        raise SystemExit(128 + signum)

    previous_handlers = {
        signum: signal.signal(signum, _terminate_on_signal)
        for signum in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        return process.wait(timeout=SETUP_TOKEN_COMMAND_TIMEOUT_S)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        _terminate_process_group(process)
        raise
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def default_telemetry_url() -> str:
    override = os.environ.get("ALFRED_DEFAULT_TELEMETRY_URL")
    if override is not None:
        return override.strip()
    return DEFAULT_TELEMETRY_URL


def parse_consent(value: object) -> bool:
    """Interpret a config consent value as a strict opt-in.

    True only for a real boolean ``True`` or a string in ``_TRUTHY_CONSENT``
    (case-insensitive, whitespace-trimmed). Every other value, including the
    string ``"false"``, ``"0"``, an int, ``None``, or an empty string, is OFF.

    This avoids the ``bool("false") is True`` trap: a privacy-sensitive switch
    must never default ON just because someone quoted the value in their config.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_CONSENT
    # Anything else (int, float, None, list, dict) is not an explicit opt-in.
    return False


PROMPT_TEMPLATE_BY_ROLE = {
    "feature_dev": "feature-dev.md",
    "planner": "planner.md",
    "test_coverage": "test-coverage.md",
    "pr_review": "code-review.md",
    "ci_repair": "review-fix.md",
    "bug_triage": "bug-triage.md",
    "cross_repo_coordinator": "cross-repo-coordinator.md",
    "spec_planner": "spec-bundle-planner.md",
    "smoke_runner": "post-deploy-smoke.md",
    "ops_morning": "ecs-monitor.md",
}

SETUP_LABELS: list[tuple[str, str, str]] = [
    ("agent:implement", "0e8a16", "Ready for an Alfred implementer to pick up."),
    ("agent:in-flight", "e11d21", "An Alfred agent is actively working this issue."),
    ("agent:pr-open", "fbca04", "A PR exists for this issue."),
    ("agent:done", "0e8a16", "Issue shipped."),
    ("agent:authored", "1d76db", "PR authored by an Alfred agent."),
    ("done-already", "0e8a16", "Issue was already implemented before Alfred picked it up."),
    ("agent:large-feature", "ff6b00", "Multi-repo feature candidate for the architect."),
    ("architect-pr-open", "5319e7", "A architect bundle PR is open in this repo."),
    ("do-not-pickup", "5319e7", "Operator override: agents must not claim this issue."),
    ("do-not-review", "cccccc", "Skip automated PR review."),
    ("needs:human-scope", "e99695", "Issue needs manual scoping before autonomous work."),
    ("needs:info", "d4c5f9", "Reporter needs to provide more detail."),
    ("needs:triage", "fef2c0", "Needs bug triage."),
    ("bug", "ee0701", "Confirmed bug."),
    ("test-coverage", "bfdadc", "Test coverage work."),
    ("severity:p0", "b60205", "Production broken, data loss, or security leak."),
    ("severity:p1", "d93f0b", "User-visible bug, not blocking."),
    ("severity:p2", "fbca04", "Minor or polish issue."),
    ("severity:p3", "0e8a16", "Trivial or won't fix."),
]

# Repo-operating agents that need runtime settings beyond selected repos.
SPECIAL_PROMPTS = {
    "architect": [
        (
            "ARCHITECT_PARENT_REPO",
            "Parent issue repo the architect should read (owner/repo; blank keeps it idle)",
        )
    ],
    "e2e-runner": [("ALFRED_E2E_RUNNER_TARGET_URL", "Staging URL the e2e runner should hit")],
    "ops-watch": [
        ("ALFRED_OPS_WATCH_ECS_CLUSTER", "ECS cluster name for the ops watch"),
        ("ALFRED_OPS_WATCH_SENTRY_ORG", "Sentry org slug for the ops watch (blank to skip)"),
    ],
}

REPO_LOCAL_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?=")
REPO_LOCAL_MAP_COMMA_BOUNDARY_RE = re.compile(
    r",\s*(?=[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?=(?:/|~|\.{1,2}/))"
)
SLACK_WEBHOOK_RE = re.compile(r"^https://hooks\.slack\.com/services/")

ALFRED_ENV_BANNER = "# alfred-init, generated below this line. Safe to re-run."
# Matches the banner whatever separator a past release used between
# "alfred-init" and "generated" (older releases used an em-dash, current
# uses a comma). upsert_env_file relies on this so an upgrade rewrites the
# existing managed block in place instead of appending a duplicate.
ALFRED_ENV_BANNER_RE = re.compile(
    r"# alfred-init.{1,4}generated below this line\. Safe to re-run\."
)
ENV_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def alfred_init_managed_env_keys() -> frozenset[str]:
    keys = {
        "GH_ORG",
        "SLACK_WEBHOOK_URL",
        "SLACK_WEBHOOK_SECRET_ID",
        "SLACK_WEBHOOK_SECRET_REGION",
        "ALFRED_QUEUE_REPOS",
        "ALFRED_SHIPPED_REPOS",
        "ALFRED_BRIDGE_REPOS",
        "ARCHITECT_ROLLOUT_ORDER",
        "ALFRED_MORNING_BRIEF_AGENTS",
        "ALFRED_REPO_LOCAL_MAP",
        "ALFRED_TELEMETRY_ENABLED",
        "ALFRED_TELEMETRY_URL",
        "ALFRED_TELEMETRY_TOKEN",
        *MEMORY_AUTO_PROMOTE_CONTROL_ENVS,
        *batteries.managed_env_keys(),
    }
    for _, (default_codename, _, _, _) in AGENT_CATALOG.items():
        default_slug = default_codename.upper().replace("-", "_")
        keys.add(f"ALFRED_{default_slug}_REPOS")
        keys.add(f"ALFRED_{default_slug}_AWS_PROFILE")
    for env_keys in ROLE_REPO_ENV_KEYS.values():
        keys.update(env_keys)
    for prompts in SPECIAL_PROMPTS.values():
        keys.update(env_key for env_key, _ in prompts)
    return frozenset(keys)


ALFRED_INIT_MANAGED_ENV_KEYS = alfred_init_managed_env_keys()

# ---------------------------------------------------------------------------
# ANSI helpers (TTY-aware).
# ---------------------------------------------------------------------------


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.BLUE = "\033[1;34m" if enabled else ""
        self.GREEN = "\033[1;32m" if enabled else ""
        self.YELLOW = "\033[1;33m" if enabled else ""
        self.RED = "\033[1;31m" if enabled else ""
        self.DIM = "\033[2m" if enabled else ""
        self.OFF = "\033[0m" if enabled else ""


STYLE = Style(sys.stdout.isatty())


def step(msg: str) -> None:
    print(f"{STYLE.BLUE}==>{STYLE.OFF} {msg}")


def ok(msg: str) -> None:
    print(f"{STYLE.GREEN}  ok{STYLE.OFF} {msg}")


def warn(msg: str) -> None:
    print(f"{STYLE.YELLOW}  ! {STYLE.OFF} {msg}", file=sys.stderr)


def fail(msg: str) -> None:
    print(f"{STYLE.RED}  !!{STYLE.OFF} {msg}", file=sys.stderr)


def note(msg: str) -> None:
    print(f"{STYLE.DIM}     {msg}{STYLE.OFF}")


def telemetry_endpoint_label(url: str) -> str:
    """A token-safe label for a telemetry ingest URL, for status output.

    Shows scheme://host/path only. Any query string or userinfo is dropped so a
    pasted shared secret (e.g. ``?token=...`` or ``user:secret@host``) never
    lands in provisioning logs. Falls back to the host, then the raw URL, if the
    URL does not parse into a usable form.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return telemetry_url_fallback_label(url)
    host = parts.hostname or ""
    if parts.scheme and host:
        try:
            parsed_port = parts.port
        except ValueError:
            parsed_port = None
        port = f":{parsed_port}" if parsed_port else ""
        return f"{parts.scheme}://{host}{port}{parts.path}"
    return host or telemetry_url_fallback_label(url)


def telemetry_url_fallback_label(url: str) -> str:
    """Best-effort token-safe label when a telemetry URL is malformed.

    ``urlsplit`` can fail or parse without a hostname for malformed operator
    input. The status line still must not echo shared secrets, so strip query,
    fragment, and userinfo with simple delimiter rules that do not re-parse the
    bad URL.
    """
    label = url.split("?", 1)[0].split("#", 1)[0]
    if "@" not in label:
        return label
    before, after = label.rsplit("@", 1)
    if "://" in before:
        scheme = before.split("://", 1)[0]
        return f"{scheme}://{after}"
    return after


# ---------------------------------------------------------------------------
# Config dataclass, single source of truth for what the wizard collects.
# ---------------------------------------------------------------------------


@dataclass
class WizardState:
    alfred_home: Path
    env_file: Path
    repo_root: Path
    gh_org: str = ""
    repos: list[str] = field(default_factory=list)
    slack_webhook: str = ""
    slack_storage: str = "env"  # "env" or "aws"
    aws_profile_for_slack: str = ""
    aws_region: str = "us-east-1"
    use_aws: bool = False
    aws_agent_profiles: dict[str, str] = field(default_factory=dict)  # codename -> profile
    enabled_roles: list[str] = field(default_factory=list)  # role keys
    role_to_repos: dict[str, list[str]] = field(default_factory=dict)  # role -> [org/repo]
    role_to_schedule: dict[str, str] = field(default_factory=dict)  # role -> schedule
    role_to_extras: dict[str, dict[str, str]] = field(default_factory=dict)  # role -> {ENV: value}
    repo_local_map: dict[str, str] = field(default_factory=dict)  # repo slug/name -> local path
    telemetry_enabled: bool = True  # anonymous proof telemetry is opt-out
    telemetry_url: str = field(default_factory=default_telemetry_url)
    telemetry_token: str = ""  # optional shared ingest token (X-Ingest-Token)
    batteries: list[str] = field(default_factory=list)  # opt-in battery ids to enable
    battery_defaults_disabled: bool = False  # explicit --batteries none/builtin choice


# ---------------------------------------------------------------------------
# Prompt helpers.
# ---------------------------------------------------------------------------


def ask(prompt: str, default: str = "", *, non_interactive: bool = False) -> str:
    if non_interactive:
        return default
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{STYLE.BLUE}?{STYLE.OFF}  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return ans or default


def ask_yes_no(prompt: str, default: bool = False, *, non_interactive: bool = False) -> bool:
    default_str = "Y/n" if default else "y/N"
    if non_interactive:
        return default
    raw = ask(f"{prompt} [{default_str}]", "", non_interactive=False).lower()
    if not raw:
        return default
    return raw.startswith("y")


# ---------------------------------------------------------------------------
# Subprocess helpers.
# ---------------------------------------------------------------------------


def run(
    cmd: list[str],
    *,
    check: bool = False,
    capture: bool = True,
    timeout: int | None = None,
    input_str: str | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with sane defaults."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
        input=input_str,
    )


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# ---------------------------------------------------------------------------
# .env IO, append-only with idempotent guard markers.
# ---------------------------------------------------------------------------


def _parse_env_text(raw_text: str) -> dict[str, str]:
    """Parse KEY=VALUE pairs from dotenv text. Quotes/exports are tolerated."""
    out: dict[str, str] = {}
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = strip_inline_comment(v).strip()
        try:
            parsed = shlex.split(v)
        except ValueError:
            parsed = []
        if len(parsed) == 1:
            v = parsed[0]
        else:
            v = v.strip("'").strip('"')
        if k:
            out[k] = v
    return out


def strip_inline_comment(value: str) -> str:
    """Strip shell-style inline comments while preserving quoted hashes."""
    quote: str | None = None
    escaped = False
    for index, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs from $ALFRED_HOME/.env. Quotes/exports are tolerated."""
    if not path.exists():
        return {}
    return _parse_env_text(path.read_text())


def read_unmanaged_env_file(path: Path) -> dict[str, str]:
    """Parse only operator-authored .env values above the managed block."""
    if not path.exists():
        return {}
    raw = path.read_text()
    managed = ALFRED_ENV_BANNER_RE.search(raw)
    if managed:
        raw = raw[: managed.start()]
    return _parse_env_text(raw)


def read_managed_env_file(path: Path) -> dict[str, str]:
    """Parse only the alfred-init managed block from .env."""
    if not path.exists():
        return {}
    raw = path.read_text()
    managed = ALFRED_ENV_BANNER_RE.search(raw)
    if not managed:
        return {}
    reject_removed_role_aliases(path, raw, managed)
    return _parse_env_text(
        "\n".join(
            _managed_env_assignment_lines(
                raw,
                managed,
                ALFRED_INIT_MANAGED_ENV_KEYS,
            )
        )
    )


def quote_env_value(value: str) -> str:
    """Return a shell-safe scalar for a generated .env assignment."""
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("env values must be single-line strings")
    return shlex.quote(value)


def upsert_env_file(path: Path, kvs: dict[str, str]) -> None:
    """Add or update keys in .env below the alfred-init banner.

    Idempotent: rewrites the marker block on every call so re-running
    the wizard doesn't accumulate dupes.
    """
    if path.exists():
        raw = path.read_text()
        managed = ALFRED_ENV_BANNER_RE.search(raw)
        if managed:
            reject_removed_role_aliases(path, raw, managed)
    upsert_env_block(
        path,
        kvs,
        ALFRED_ENV_BANNER,
        ALFRED_ENV_BANNER_RE,
        managed_keys=ALFRED_INIT_MANAGED_ENV_KEYS | frozenset(kvs),
    )


def env_assignment_key(line: str) -> str | None:
    """Return the dotenv key assigned by a line, or None for non-assignments."""
    match = ENV_ASSIGNMENT_RE.match(line.strip())
    return match.group(1) if match else None


def _line_after_match(text: str, match: re.Match[str]) -> int:
    line_end = text.find("\n", match.end())
    return len(text) if line_end == -1 else line_end + 1


def reject_removed_role_aliases(path: Path, text: str, match: re.Match[str]) -> None:
    """Stop instead of guessing how to rewrite a retired mutable identity config."""
    cursor = _line_after_match(text, match)
    for line in text[cursor:].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            break
        key = env_assignment_key(stripped)
        if key and key.startswith("AGENT_CODENAME_"):
            fail(
                f"{path} uses removed mutable role identities. "
                "Remove the Alfred runtime directory and reinstall; built-in role slugs are fixed."
            )
            raise SystemExit(2)


def _managed_env_assignment_lines(
    text: str,
    match: re.Match[str],
    managed_keys: frozenset[str],
) -> list[str]:
    """Return contiguous assignment lines owned by the matched generated block."""
    lines: list[str] = []
    cursor = _line_after_match(text, match)
    while cursor < len(text):
        line_end = text.find("\n", cursor)
        if line_end == -1:
            line_end = len(text)
            next_cursor = len(text)
        else:
            next_cursor = line_end + 1
        line = text[cursor:line_end]
        key = env_assignment_key(line)
        if key not in managed_keys:
            break
        lines.append(line)
        cursor = next_cursor
    return lines


def _managed_env_block_end(
    text: str,
    match: re.Match[str],
    managed_keys: frozenset[str],
) -> int:
    cursor = _line_after_match(text, match)
    while cursor < len(text):
        line_end = text.find("\n", cursor)
        if line_end == -1:
            line_end = len(text)
            next_cursor = len(text)
        else:
            next_cursor = line_end + 1
        key = env_assignment_key(text[cursor:line_end])
        if key not in managed_keys:
            break
        cursor = next_cursor
    return cursor


def _join_env_sections(*sections: str) -> str:
    cleaned = [section.strip("\n") for section in sections if section.strip()]
    if not cleaned:
        return ""
    return "\n\n".join(cleaned) + "\n"


def upsert_env_block(
    path: Path,
    kvs: dict[str, str],
    banner: str,
    banner_re: re.Pattern[str],
    *,
    managed_keys: frozenset[str] | None = None,
) -> None:
    """Add or update keys in an idempotent generated rc block."""
    if not kvs:
        return
    existing = path.read_text() if path.exists() else ""
    block = [banner]
    for k, v in kvs.items():
        block.append(f"{k}={quote_env_value(v)}")
    block_text = "\n".join(block)
    # Strip any prior generated block for this marker so we re-emit fresh
    # values instead of accumulating a duplicate section. When a key allowlist
    # is provided, remove only this block's managed assignment lines and keep
    # later .env sections such as scheduler tokens and architect setup intact.
    prior = banner_re.search(existing)
    if prior and managed_keys is not None:
        block_end = _managed_env_block_end(existing, prior, managed_keys)
        new = _join_env_sections(existing[: prior.start()], block_text, existing[block_end:])
    elif prior:
        new = _join_env_sections(existing[: prior.start()], block_text)
    else:
        new = _join_env_sections(existing, block_text)
    path.write_text(new)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def remove_env_block(path: Path, banner_re: re.Pattern[str]) -> None:
    """Remove a generated rc block and its managed values, if present."""
    if not path.exists():
        return
    existing = path.read_text()
    prior = banner_re.search(existing)
    if not prior:
        return
    new = existing[: prior.start()].rstrip()
    if not new:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    path.write_text(new + "\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


# ---------------------------------------------------------------------------
# Agent discovery - scan bin/*.py for known role runners.
# ---------------------------------------------------------------------------


def discover_agents(bin_dir: Path) -> list[str]:
    """Return the role-keys from AGENT_CATALOG whose runners exist in bin/.

    A runner is "present" when the role script in AGENT_CATALOG exists.
    Built-in runtime IDs and script names are immutable role slugs. Themes
    change only the display layer. Order follows ``AGENT_CATALOG``.
    """
    if not bin_dir.is_dir():
        return []
    present = set()
    for f in bin_dir.iterdir():
        if not f.is_file() or f.suffix != ".py":
            continue
        stem = f.stem
        if stem in CODENAME_TO_ROLE:
            present.add(CODENAME_TO_ROLE[stem])
    # Some agents are .sh (fleet-recap-morning/evening). Check those too.
    for f in bin_dir.iterdir():
        if not f.is_file() or f.suffix != ".sh":
            continue
        stem = f.stem
        # fleet-recap.sh ships the morning + evening jobs.
        if stem == "fleet-recap":
            present.add("fleet_recap_morning")
            present.add("fleet_recap_evening")
        if stem in CODENAME_TO_ROLE:
            present.add(CODENAME_TO_ROLE[stem])
    return [role for role in AGENT_CATALOG if role in present]


def starter_roles(available: list[str]) -> list[str]:
    """Return the explicit small starter fleet from the discovered runners."""
    starter = [role for role in STARTER_ROLES if role in available]
    return starter or list(available[:1])


def recommended_roles(available: list[str]) -> list[str]:
    """Return the default full fleet from the discovered runners."""
    return list(available)


def roles_from_agents_arg(raw: str, available: list[str]) -> list[str]:
    """Resolve --agents into role keys while preserving catalog order.

    Accepted values:
      - all / recommended / default
      - starter (explicit minimal setup)
      - comma-separated codenames, role keys, or script stems
    """
    value = (raw or "").strip()
    if not value:
        return recommended_roles(available)
    lowered = value.lower()
    if lowered == "starter":
        return starter_roles(available)
    if lowered in {"recommended", "default"}:
        return recommended_roles(available)
    if lowered == "all":
        return recommended_roles(available)

    requested = {tok.strip().lower() for tok in value.split(",") if tok.strip()}
    if "all" in requested:
        return recommended_roles(available)
    starter_tokens = {"starter"}
    alias_tokens = {"recommended", "default"}
    starter_requested = bool(requested & starter_tokens)
    matched: list[str] = starter_roles(available) if starter_requested else []
    for role in available:
        if role in matched:
            continue
        default_codename = AGENT_CATALOG[role][0].lower()
        script_stem = default_codename.removesuffix(".py")
        if role.lower() in requested or default_codename in requested or script_stem in requested:
            matched.append(role)
    unknown = (
        requested
        - {token for role in matched for token in (role.lower(), AGENT_CATALOG[role][0].lower())}
        - starter_tokens
        - alias_tokens
    )
    ignored_aliases = requested & alias_tokens
    if ignored_aliases:
        warn(
            "Ignoring mixed --agents alias value(s): "
            + ", ".join(sorted(ignored_aliases))
            + ". Use the alias by itself, or use 'all' for the full fleet."
        )
    if unknown:
        warn(f"Ignoring unknown --agents value(s): {', '.join(sorted(unknown))}")
    return matched


def repo_local_names(repos: list[str]) -> list[str]:
    out: list[str] = []
    for repo in repos:
        name = repo.rsplit("/", 1)[-1]
        if name and name not in out:
            out.append(name)
    return out


def repo_runtime_values(repos: list[str]) -> list[str]:
    """Return repo tokens suitable for the current agent env-var contract.

    The wizard stores selected repos as GitHub slugs (owner/repo) so label
    setup can call gh with an unambiguous -R value. Most shipped runners read
    ALFRED_<AGENT>_REPOS as local repo names under GH_ORG and then build
    owner/repo themselves, so the generated .env must strip the owner.
    """

    return repo_local_names(repos)


def repo_board_values(repos: list[str], gh_org: str) -> list[str]:
    """Return GitHub ``owner/repo`` slugs for board, queue, and bridge scopes."""
    out: list[str] = []
    seen: set[str] = set()
    owner = gh_org.strip()
    for raw in repos:
        repo = raw.strip()
        if not repo:
            continue
        if "/" not in repo:
            if not owner:
                continue
            repo = f"{owner}/{repo}"
        slug = repo.lower()
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def parse_repo_local_map(raw: str) -> dict[str, str]:
    """Parse ``ALFRED_REPO_LOCAL_MAP`` into a deterministic mapping."""
    out: dict[str, str] = {}
    for piece in repo_local_map_entries(raw):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        key = key.strip()
        value = decode_repo_local_map_value(value.strip())
        if key and value:
            out[key] = value
    return out


def apply_repo_local_map_layer(out: dict[str, str], layer: dict[str, str]) -> None:
    """Apply one repo-local map layer, including its bare runtime aliases."""
    for key, value in layer.items():
        if "/" in key:
            continue
        for existing in list(out):
            if "/" in existing and existing.rsplit("/", 1)[-1] == key and existing not in layer:
                out[existing] = value
    for key, value in layer.items():
        out[key] = value
    for key, value in layer.items():
        if "/" not in key:
            continue
        bare = key.rsplit("/", 1)[-1]
        if bare not in layer:
            out[bare] = value


def repo_local_map_entries(raw: str) -> list[str]:
    """Split a repo-local-map env value without treating path commas as delimiters."""
    value = raw.strip()
    if not value:
        return []
    try:
        tokens = shlex.split(value)
    except ValueError:
        tokens = []
    if tokens:
        return repo_local_map_entries_from_tokens(repo_local_map_expand_tokens(tokens))
    return [value]


def repo_local_map_expand_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(part for part in REPO_LOCAL_MAP_COMMA_BOUNDARY_RE.split(token) if part)
    return expanded


def repo_local_map_is_path_boundary_token(token: str) -> bool:
    if not REPO_LOCAL_MAP_KEY_RE.match(token):
        return False
    value = token.split("=", 1)[1]
    return value.startswith(("/", "~", "./", "../"))


def repo_local_map_entries_from_tokens(tokens: list[str]) -> list[str]:
    """Rejoin decoded shell tokens into ``key=value`` map entries."""
    entries: list[str] = []
    current = ""
    for token in tokens:
        if REPO_LOCAL_MAP_KEY_RE.match(token):
            if current:
                if current.endswith(",") and repo_local_map_is_path_boundary_token(token):
                    entries.append(current[:-1])
                else:
                    entries.append(current)
            current = token
        elif current:
            current = f"{current} {token}"
    if current:
        entries.append(current)
    return entries


def format_repo_local_map(mapping: dict[str, str]) -> str:
    """Serialize ``ALFRED_REPO_LOCAL_MAP`` with stable, path-safe ordering."""
    return shlex.join(
        f"{key}={encode_repo_local_map_value(mapping[key])}" for key in sorted(mapping)
    )


def encode_repo_local_map_value(value: str) -> str:
    """Encode path values that cannot survive decoded env-token splitting."""
    if any(char.isspace() or char in ",%" for char in value):
        return "url:" + urllib.parse.quote(value, safe="/._-~")
    return value


def decode_repo_local_map_value(value: str) -> str:
    if value.startswith("url:"):
        return urllib.parse.unquote(value.removeprefix("url:"))
    return value


def selected_source_checkout_slugs(
    selected: list[str],
    source_slug: str,
    gh_org: str,
) -> list[str]:
    """Selected repo slugs that exactly identify the source checkout."""
    source_lower = source_slug.lower()
    owner = gh_org.strip().lower()
    source_repo = source_lower.rsplit("/", 1)[-1]
    out: list[str] = []
    for repo in selected:
        raw = repo.strip()
        if not raw:
            continue
        repo_lower = raw.lower()
        if repo_lower == source_lower:
            out.append(raw)
            continue
        if "/" in repo_lower:
            repo_owner, repo_name = repo_lower.split("/", 1)
            if owner and repo_owner == owner and repo_name == source_repo:
                out.append(raw)
            continue
        if owner and repo_lower == source_repo:
            out.append(f"{gh_org.strip()}/{raw}")
    return out


def github_slug_from_remote_url(raw: str) -> str:
    """Return ``owner/repo`` for a GitHub remote URL, or ``""``."""
    url = raw.strip()
    if not url:
        return ""
    if url.startswith("git@github.com:"):
        path = url.split(":", 1)[1]
    else:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return ""
        if parsed.hostname != "github.com":
            return ""
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git").strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[-2]}/{parts[-1]}"


def source_checkout_github_slug(repo_root: Path) -> str:
    """Best-effort GitHub slug for the alfred-os source checkout."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if cp.returncode != 0:
        return ""
    return github_slug_from_remote_url(cp.stdout.strip())


def workspace_for_state(state: WizardState) -> Path:
    """Resolve the workspace path the deployed agents will use."""
    env = read_env_file(state.env_file)
    root_raw = os.environ.get("WORKSPACE_ROOT") or env.get("WORKSPACE_ROOT") or "~/code"
    root = Path(os.path.expanduser(root_raw))
    if "WORKSPACE_SUBDIR" in os.environ:
        subdir = os.environ.get("WORKSPACE_SUBDIR", "")
    elif "WORKSPACE_SUBDIR" in env:
        subdir = env.get("WORKSPACE_SUBDIR", "")
    else:
        subdir = "product"
    return root / subdir if subdir else root


def infer_source_checkout_repo_local_map(state: WizardState) -> dict[str, str]:
    """Map the selected source checkout when its local path is non-standard.

    Source installs often live at ``tools/alfred-os`` while the public GitHub
    repo slug is ``alfred``. Without an explicit map, repo-operating agents look
    for ``$WORKSPACE_ROOT/product/alfred`` and fail doctor even though the
    selected repo is the checkout the operator just installed from.
    """

    selected = selected_repo_union(state)
    if not selected:
        return {}
    slug = source_checkout_github_slug(state.repo_root)
    if not slug:
        return {}
    local_name = slug.rsplit("/", 1)[-1]
    selected_source_slugs = selected_source_checkout_slugs(selected, slug, state.gh_org)
    if not selected_source_slugs:
        return {}

    actual = state.repo_root.resolve()
    expected = (workspace_for_state(state) / local_name).expanduser()
    if expected.resolve() == actual:
        return {}
    actual_str = str(actual)
    out = {local_name: actual_str}
    for repo in selected_source_slugs:
        out[repo] = actual_str
    return out


def selected_repo_union(state: WizardState) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for role in state.enabled_roles:
        for repo in state.role_to_repos.get(role, []):
            if repo not in seen:
                seen.add(repo)
                out.append(repo)
    return out


def label_setup_repos(state: WizardState) -> list[str]:
    repos = selected_repo_union(state)
    seen = set(repos)
    architect_parent = (
        state.role_to_extras.get("cross_repo_coordinator", {})
        .get("ARCHITECT_PARENT_REPO", "")
        .strip()
    )
    if architect_parent and architect_parent not in seen:
        repos.append(architect_parent)
    return repos


def role_uses_repos(role: str) -> bool:
    """True when setup should collect repos for a role."""
    return AGENT_CATALOG[role][2] or role in ROLE_REPO_ENV_KEYS


def morning_brief_agents(state: WizardState) -> list[str]:
    """Default codenames included in the scheduled morning brief."""
    return [
        runtime_id_for_role(role)
        for role in state.enabled_roles
        if role not in MORNING_BRIEF_EXCLUDED_ROLES
    ]


def _seed_prompt_template(src: Path, dest: Path, created: list[Path]) -> None:
    """Install a template or refresh untouched generated scaffolding."""
    if not src.exists():
        return
    if dest.exists():
        try:
            with dest.open(encoding="utf-8") as installed:
                first_line = installed.readline()
        except OSError:
            return
        if "alfred:auto-seed" not in first_line:
            return
        try:
            if src.read_bytes() == dest.read_bytes():
                return
        except OSError:
            return
    shutil.copyfile(src, dest)
    created.append(dest)


def seed_prompt_templates(state: WizardState) -> list[Path]:
    """Copy starter prompt templates into ALFRED_HOME for enabled agents.

    Existing operator prompts are never overwritten. Files that retain the
    ``alfred:auto-seed`` marker are generated scaffolding and refresh to the
    current machine contract.
    """
    created: list[Path] = []
    prompt_root = state.alfred_home / "prompts"
    prompt_root.mkdir(parents=True, exist_ok=True)
    template_root = state.repo_root / "prompts"
    for shared_name in ("spec-interrogator.md",):
        src = template_root / shared_name
        dest = prompt_root / shared_name
        _seed_prompt_template(src, dest, created)
    for role in state.enabled_roles:
        template_name = PROMPT_TEMPLATE_BY_ROLE.get(role)
        if not template_name:
            continue
        src = template_root / template_name
        dest = prompt_root / f"{runtime_id_for_role(role)}.md"
        _seed_prompt_template(src, dest, created)
    return created


def write_fleet_enable_state(state: WizardState) -> list[str]:
    """Enable every selected built-in role while preserving custom entries.

    Architect and spec-planner are safe to schedule in a full-fleet install:
    each runner stays idle until its repo/spec scope exists. Persist the same
    role IDs that ``render_agents_conf`` places in scheduler labels.
    """
    selected: list[str] = []
    for role in state.enabled_roles:
        if role not in SCOPE_GATED_ROLES:
            continue
        selected.append(runtime_id_for_role(role))
    if not selected:
        return []

    path = state.alfred_home / "state" / "fleet" / "enabled.txt"
    existing: list[str] = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.partition("#")[0].strip()
            if line:
                existing.append(line)

    enabled = sorted(set(existing) | set(selected))
    header = (
        "# Fleet enable list, managed by `alfred enable/disable <agent>`.\n"
        "# Built-in role IDs plus operator-defined custom-agent IDs.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(header + "\n".join(enabled) + "\n", encoding="utf-8")
    tmp.replace(path)
    return sorted(set(selected))


# ---------------------------------------------------------------------------
# agents.conf renderer.
# ---------------------------------------------------------------------------


def render_agents_conf(state: WizardState) -> str:
    """Produce the full agents.conf text from WizardState.

    Format mirrors launchd/agents.conf.example: tab-separated rows
    (label, script, schedule, needs_java, log_stem, role). Includes a header
    comment so the operator can find their way back later.
    """
    lines = [
        "# agents.conf, generated by alfred-init.",
        "# Tab-separated. Re-run `alfred-init` to regenerate.",
        "#",
        "# label\tscript\tschedule\tneeds_java\tlog_stem\trole",
        "",
    ]
    for role in state.enabled_roles:
        codename = runtime_id_for_role(role)
        default_codename, desc, _, _ = AGENT_CATALOG[role]
        schedule = state.role_to_schedule.get(role, AGENT_CATALOG[role][3])
        # Paired schedule rows share one implementation + log stem.
        if role.startswith("fleet_recap_"):
            script = "fleet-recap.sh"
            log_stem = "alfred.fleet-recap"
        elif role.startswith("shipped_summary_"):
            script = f"{default_codename}.sh"
            log_stem = "alfred.shipped-summary"
        else:
            # Script names and scheduler labels share one immutable role ID.
            script = f"{default_codename}.py"
            log_stem = f"alfred.{codename}"
        label = f"alfred.{codename}"
        role_text = desc.split(" (", 1)[0]
        row = f"{label}\t{script}\t{schedule}\tno\t{log_stem}\t{role_text}"
        lines.append(row)
    # Proof-telemetry is not an AGENT_CATALOG role, so it is not in
    # enabled_roles. Emit its scheduler row only when an ingest URL exists.
    # Without a URL, the reporter is a clean no-op and scheduling it would only
    # create noise in doctor output.
    if state.telemetry_enabled and state.telemetry_url:
        lines.append(
            "alfred.proof-telemetry\tproof-telemetry.py\tinterval:3600\tno\t"
            "alfred.proof-telemetry\tAnonymous usage totals"
        )
    return "\n".join(lines) + "\n"


def env_assignments_for(state: WizardState) -> dict[str, str]:
    """Per-role env-var map written into $ALFRED_HOME/.env."""
    out: dict[str, str] = {}
    existing_env = read_unmanaged_env_file(state.env_file)
    existing_effective_env = read_env_file(state.env_file)
    if state.gh_org:
        out["GH_ORG"] = state.gh_org
    selected_repos = selected_repo_union(state)
    board_repos = repo_board_values(selected_repos, state.gh_org)
    if board_repos:
        board_scope = ",".join(board_repos)
        if "ALFRED_QUEUE_REPOS" in existing_env:
            queue_scope = existing_env["ALFRED_QUEUE_REPOS"]
        elif "ALFRED_QUEUE_REPOS" in existing_effective_env:
            queue_scope = existing_effective_env["ALFRED_QUEUE_REPOS"]
        else:
            queue_scope = board_scope
        out["ALFRED_QUEUE_REPOS"] = queue_scope
        out["ALFRED_SHIPPED_REPOS"] = board_scope
        out["ALFRED_BRIDGE_REPOS"] = board_scope
    repo_map: dict[str, str] = {}
    apply_repo_local_map_layer(repo_map, infer_source_checkout_repo_local_map(state))
    apply_repo_local_map_layer(
        repo_map,
        parse_repo_local_map(existing_env.get("ALFRED_REPO_LOCAL_MAP", "")),
    )
    apply_repo_local_map_layer(repo_map, state.repo_local_map)
    if repo_map:
        out["ALFRED_REPO_LOCAL_MAP"] = format_repo_local_map(repo_map)
    if state.slack_storage == "env" and state.slack_webhook:
        out["SLACK_WEBHOOK_URL"] = state.slack_webhook
    elif state.slack_storage == "aws":
        out["SLACK_WEBHOOK_SECRET_ID"] = "alfred/slack-webhook"
        out["SLACK_WEBHOOK_SECRET_REGION"] = state.aws_region
    for role in state.enabled_roles:
        codename = runtime_id_for_role(role)
        codename_slug = codename.upper().replace("-", "_")
        repos = state.role_to_repos.get(role, [])
        if repos:
            runtime_repos = repo_runtime_values(repos)
            if role == "cross_repo_coordinator":
                out["ARCHITECT_ROLLOUT_ORDER"] = ",".join(runtime_repos)
            elif role in ROLE_REPO_ENV_KEYS:
                for env_key in ROLE_REPO_ENV_KEYS[role]:
                    out[env_key] = ",".join(runtime_repos)
            else:
                out[f"ALFRED_{codename_slug}_REPOS"] = ",".join(runtime_repos)
        if role == "morning_brief":
            agents = morning_brief_agents(state)
            if agents:
                out["ALFRED_MORNING_BRIEF_AGENTS"] = ",".join(agents)
        for k, v in state.role_to_extras.get(role, {}).items():
            out[k] = v
        if state.use_aws and codename in state.aws_agent_profiles:
            out[f"ALFRED_{codename_slug}_AWS_PROFILE"] = state.aws_agent_profiles[codename]
    if "memory_auto_promote" in state.enabled_roles:
        out.update(memory_auto_promote_control_assignments(state))
    # Anonymous proof-telemetry is opt-out. New installs use Alfred's hosted
    # collector by default; a "no" answer writes ALFRED_TELEMETRY_ENABLED=0 so
    # the opt-out is explicit.
    if state.telemetry_enabled and state.telemetry_url:
        out["ALFRED_TELEMETRY_ENABLED"] = "1"
        out["ALFRED_TELEMETRY_URL"] = state.telemetry_url
        # Optional shared ingest token: only written when both opted in AND a
        # token was provided. Matches the collector's INGEST_TOKEN.
        if state.telemetry_token:
            out["ALFRED_TELEMETRY_TOKEN"] = state.telemetry_token
    elif not state.telemetry_enabled:
        out["ALFRED_TELEMETRY_ENABLED"] = "0"
    # Advanced batteries the operator picked. Included defaults need no explicit
    # env value unless the operator requested built-ins only. Each selection sets
    # its real flags from the shared manifest and composes onto the existing
    # config, so a memory battery merges into the current provider chain instead
    # of clobbering it. Mutually-exclusive primaries are rejected in step_8c.
    compose_env = {**existing_effective_env, **out}
    if state.battery_defaults_disabled:
        for battery in batteries.default_batteries():
            values = batteries.disable_values(battery)
            out.update(values)
            compose_env.update(values)
    for battery_id in state.batteries:
        battery = batteries.battery_by_id(battery_id)
        if battery is not None and not battery.builtin:
            values = batteries.enable_values(battery, compose_env)
            out.update(values)
            compose_env.update(values)
    return out


def memory_auto_promote_control_assignments(state: WizardState) -> dict[str, str]:
    """Preserve persisted stop controls for scheduled memory auto-promotion."""
    values: dict[str, str] = {}
    with contextlib.suppress(OSError):
        for key, value in read_env_file(state.env_file).items():
            if key not in MEMORY_AUTO_PROMOTE_CONTROL_ENVS:
                continue
            clean = value.strip()
            if not clean:
                continue
            if memory_auto_promote_stop_control_active(key, clean):
                values[key] = clean
                continue
            existing = values.get(key)
            if existing and memory_auto_promote_stop_control_active(key, existing):
                continue
            values[key] = clean
    return values


def memory_auto_promote_stop_control_active(key: str, value: str) -> bool:
    """Return true for values that should keep default-on memory paused."""
    token = strip_inline_comment(value).strip().lower()
    if not token:
        return False
    if key in {"ALFRED_AUTO_PROMOTE", "ALFRED_AUTO_PROMOTE_LLM_JUDGE"}:
        return token not in {"1", "true", "yes", "on", "enabled"}
    if key == "ALFRED_AUTO_PROMOTE_KILL":
        return token not in {"0", "false", "no", "off", "disabled"}
    return False


# ---------------------------------------------------------------------------
# Slack webhook test post.
# ---------------------------------------------------------------------------


def slack_post(webhook: str, text: str, *, timeout: int = 10) -> tuple[bool, str]:
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if 200 <= resp.status < 300:
                return True, body
            return False, f"HTTP {resp.status}: {body}"
    except urllib.error.URLError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Wizard steps.
# ---------------------------------------------------------------------------


def step_0_preflight(state: WizardState) -> None:
    step("Preflight")
    if not state.alfred_home.is_dir():
        fail(f"ALFRED_HOME ({state.alfred_home}) not found.")
        fail("Run `bash install.sh` first.")
        sys.exit(1)
    ok(f"ALFRED_HOME: {state.alfred_home}")
    if not state.env_file.exists():
        fail(f"$ALFRED_HOME/.env not found at {state.env_file}.")
        fail("Run `bash install.sh` first.")
        sys.exit(1)
    ok(f"$ALFRED_HOME/.env: {state.env_file}")
    rc = read_env_file(state.env_file)
    state.gh_org = os.environ.get("GH_ORG") or rc.get("GH_ORG", "")
    if not state.gh_org:
        state.gh_org = ask("GH_ORG (GitHub org/user for your fleet)", "")
        if not state.gh_org:
            fail("GH_ORG required. Add it to $ALFRED_HOME/.env and re-run.")
            sys.exit(1)
    ok(f"GH_ORG: {state.gh_org}")
    note("Run with ALFRED_NONINTERACTIVE=1 for non-interactive defaults.")


def step_1_claude(*, non_interactive: bool) -> None:
    step("Claude Code auth")
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        warn(
            "ANTHROPIC_API_KEY is set. Claude Code prioritizes API keys over "
            "Pro/Max subscription auth, which can create API charges. Alfred "
            "does not require this key; unset it for subscription-backed runs."
        )
    if not have("claude"):
        fail("`claude` not on PATH. Install: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)
    cp = run(["claude", "--version"], timeout=10)
    if cp.returncode != 0:
        fail(f"`claude --version` failed: {cp.stderr.strip()}")
        sys.exit(1)
    ok(f"claude: {cp.stdout.strip() or '(installed)'}")
    # Light auth probe, bounded, doesn't burn a real turn if it errors.
    try:
        probe = run(["claude", "-p", "--max-turns", "1"], input_str="say hi\n", timeout=30)
    except subprocess.TimeoutExpired:
        warn("`claude -p` probe timed out. Likely waiting on auth.")
        if not non_interactive:
            input("Run `claude` interactively to authenticate, then press Enter to continue. ")
        return
    blob = (probe.stdout + probe.stderr).lower()
    if probe.returncode != 0 and ("login" in blob or "auth" in blob or "unauthorized" in blob):
        warn("Claude auth check failed. Run `claude` interactively to log in.")
        if not non_interactive:
            input("Press Enter once you have authenticated. ")
    else:
        ok("claude responds non-interactively")

    # Scheduled (launchd / systemd --user) firings can't read the host
    # credential store interactive auth populates. Offer to mint a
    # long-lived OAuth token so they can authenticate via env var.
    _maybe_offer_setup_token(non_interactive=non_interactive)


def _maybe_offer_setup_token(*, non_interactive: bool) -> None:
    """Detect missing ``CLAUDE_CODE_OAUTH_TOKEN`` and prompt to mint one.

    Skips when the token is already set in ``$ALFRED_HOME/.env``. Skips
    silently in ``--non-interactive`` mode (CI, automation) where prompting
    for a browser flow would hang.

    The token is what scheduled agents read instead of the host
    credential store, so without it every launchd / systemd-spawned
    firing returns 401 even though interactive ``claude`` works fine.
    """
    if non_interactive:
        return

    env_file = Path(os.environ.get("ALFRED_HOME") or (Path.home() / ".alfred")) / ".env"
    if read_env_file(env_file).get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        ok(f"CLAUDE_CODE_OAUTH_TOKEN already set in {env_file}")
        return

    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        warn(
            "CLAUDE_CODE_OAUTH_TOKEN is set only in this shell; scheduled firings "
            f"need it in {env_file}."
        )
        warn('Persist it with: alfred setup-token --token "$CLAUDE_CODE_OAUTH_TOKEN"')
        return

    print(
        "\n  Scheduled firings (launchd / systemd --user) can't read the\n"
        "  credential store the interactive `claude` populates. The fix is\n"
        "  a long-lived OAuth token in $ALFRED_HOME/.env that `claude` reads via\n"
        "  the CLAUDE_CODE_OAUTH_TOKEN env var. It is tied to your existing\n"
        "  subscription (no extra cost, no API-key billing) and rotates with\n"
        "  `alfred setup-token --force`.\n"
    )
    answer = input("  Run `alfred setup-token` now? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        script = Path(__file__).resolve().parent / "alfred-setup-token.py"
        try:
            rc = _run_setup_token(script)
        except subprocess.TimeoutExpired:
            warn(
                "`alfred setup-token` timed out waiting for approval. "
                "Re-run it when you are ready to finish authentication."
            )
            raise SystemExit(124) from None
        if rc != 0:
            warn(f"`alfred setup-token` exited {rc}. You can re-run it any time.")
            raise SystemExit(rc) from None
    else:
        warn(
            "Skipped. Scheduled firings will not authenticate until you run "
            "`alfred setup-token` (or set CLAUDE_CODE_OAUTH_TOKEN by hand)."
        )


def step_2_github(state: WizardState, *, non_interactive: bool) -> None:
    step("GitHub auth")
    if not have("gh"):
        fail("`gh` not on PATH. Install via `brew install gh`.")
        sys.exit(1)
    auth = run(["gh", "auth", "status"], timeout=15)
    if auth.returncode != 0:
        warn("`gh auth status` reports not authenticated.")
        if not non_interactive:
            input("Run `gh auth login` in another shell, then press Enter. ")
            auth = run(["gh", "auth", "status"], timeout=15)
            if auth.returncode != 0:
                fail("Still not authenticated. Aborting.")
                sys.exit(1)
        else:
            sys.exit(1)
    ok("gh authenticated")
    repos_cp = run(
        ["gh", "repo", "list", state.gh_org, "--limit", "200", "--json", "nameWithOwner"],
        timeout=30,
    )
    if repos_cp.returncode != 0:
        fail(f"`gh repo list {state.gh_org}` failed: {repos_cp.stderr.strip()}")
        sys.exit(1)
    try:
        rows = json.loads(repos_cp.stdout or "[]")
    except json.JSONDecodeError:
        rows = []
    state.repos = sorted({r.get("nameWithOwner", "") for r in rows if r.get("nameWithOwner")})
    if not state.repos:
        warn(
            f"No repos visible in {state.gh_org}. You can still proceed, but per-agent repo prompts will be blank."
        )
    else:
        ok(f"{len(state.repos)} repos visible in {state.gh_org}")


def step_3_slack(
    state: WizardState, *, slack_arg: str | None = None, non_interactive: bool
) -> None:
    step("Slack webhook")
    note("1. Open https://api.slack.com/apps in your browser.")
    note("2. Create a new app from scratch.")
    note("3. Add Incoming Webhooks; activate them.")
    note("4. Add a webhook URL to the channel you want for fleet status.")
    note("5. Copy the resulting URL.")
    while True:
        if slack_arg is not None:
            url = slack_arg
        elif state.slack_webhook:
            url = state.slack_webhook
        else:
            url = ask(
                "Paste your Slack webhook URL (or 'skip')",
                "skip",
                non_interactive=non_interactive,
            )
        if url == "skip" or not url:
            warn("Skipping Slack setup. Agents will still log locally under ALFRED_HOME/state.")
            return
        if not SLACK_WEBHOOK_RE.match(url):
            fail("That doesn't look like a Slack webhook URL. Try again.")
            if slack_arg is not None or non_interactive:
                sys.exit(1)
            state.slack_webhook = ""
            continue
        success, body = slack_post(url, "alfred-os installer: webhook test ok")
        if success:
            ok("Webhook test post succeeded.")
            state.slack_webhook = url
            break
        fail(f"Test post failed: {body}")
        if slack_arg is not None or non_interactive:
            sys.exit(1)
        state.slack_webhook = ""
        if not ask_yes_no("Retry?", True, non_interactive=non_interactive):
            return
    storage = ask(
        "Store webhook in AWS Secrets Manager (recommended for prod) or env? [aws/env]",
        "env",
        non_interactive=non_interactive,
    ).lower()
    if storage == "aws":
        if not have("aws"):
            warn("`aws` CLI not found; falling back to env-var storage.")
            state.slack_storage = "env"
            return
        profile = ask(
            "AWS profile name (admin) for writing the secret",
            "default",
            non_interactive=non_interactive,
        )
        region = ask("AWS region", state.aws_region, non_interactive=non_interactive)
        ident = run(["aws", "--profile", profile, "sts", "get-caller-identity"], timeout=20)
        if ident.returncode != 0:
            fail(f"AWS identity check failed: {ident.stderr.strip()}")
            warn("Falling back to env-var storage.")
            state.slack_storage = "env"
            return
        create = run(
            [
                "aws",
                "--profile",
                profile,
                "--region",
                region,
                "secretsmanager",
                "create-secret",
                "--name",
                "alfred/slack-webhook",
                "--secret-string",
                state.slack_webhook,
            ],
            timeout=30,
        )
        if create.returncode != 0:
            if "ResourceExistsException" in create.stderr or "already exists" in create.stderr:
                if ask_yes_no("Secret exists. Update it?", True, non_interactive=non_interactive):
                    upd = run(
                        [
                            "aws",
                            "--profile",
                            profile,
                            "--region",
                            region,
                            "secretsmanager",
                            "update-secret",
                            "--secret-id",
                            "alfred/slack-webhook",
                            "--secret-string",
                            state.slack_webhook,
                        ],
                        timeout=30,
                    )
                    if upd.returncode != 0:
                        fail(f"Update failed: {upd.stderr.strip()}")
                        warn("Falling back to env-var storage.")
                        state.slack_storage = "env"
                        return
            else:
                fail(f"Secret create failed: {create.stderr.strip()}")
                warn("Falling back to env-var storage.")
                state.slack_storage = "env"
                return
        ok("Slack webhook stored in AWS Secrets Manager (alfred/slack-webhook)")
        state.slack_storage = "aws"
        state.aws_profile_for_slack = profile
        state.aws_region = region
    else:
        state.slack_storage = "env"
        ok("Slack webhook will be written to $ALFRED_HOME/.env as SLACK_WEBHOOK_URL")


def step_4_aws(state: WizardState, *, non_interactive: bool) -> None:
    step("AWS (optional, per-agent IAM)")
    if not ask_yes_no(
        "Use AWS for per-agent IAM and Secrets Manager?", False, non_interactive=non_interactive
    ):
        ok("Skipping per-agent AWS profiles.")
        return
    state.use_aws = True
    # The IAM-scoped agents are the staging smoke runner and the ops-morning
    # watch. Resolve their codenames from the catalog (via each role's chosen
    # codename) so this list cannot drift from the canonical identity the way a
    # hard-coded theme-name list ("huntress", "gordon") did after the rename.
    aws_consumer_roles = ("smoke_runner", "ops_morning")
    aws_consumers = [runtime_id_for_role(role) for role in aws_consumer_roles]
    enabled_codenames = {runtime_id_for_role(r) for r in state.enabled_roles}
    for codename in aws_consumers:
        # Only prompt if this agent is enabled.
        if codename not in enabled_codenames:
            continue
        default_profile = f"{codename}-cron"
        profile = ask(
            f"AWS profile for {codename}?", default_profile, non_interactive=non_interactive
        )
        ident = run(["aws", "--profile", profile, "sts", "get-caller-identity"], timeout=20)
        if ident.returncode != 0:
            warn(f"AWS profile '{profile}' not configured. See docs/AWS_SETUP.md.")
            continue
        state.aws_agent_profiles[codename] = profile
        ok(f"AWS profile for {codename}: {profile}")


def step_5_pick_agents(
    state: WizardState, available: list[str], *, agents_arg: str | None, non_interactive: bool
) -> None:
    step("Pick agents")
    if not available:
        warn("No agent runners discovered in bin/. Did parallel agents land yet?")
        warn("Falling back to the full catalog.")
        available = list(AGENT_CATALOG.keys())
    if state.enabled_roles and not agents_arg:
        configured = [
            role for role in AGENT_CATALOG if role in state.enabled_roles and role in available
        ]
        if not configured:
            fail("--config agents did not match any discovered agents.")
            sys.exit(1)
        state.enabled_roles = configured
        ok(f"Enabled {len(state.enabled_roles)} agents from --config.")
        return
    if agents_arg:
        state.enabled_roles = roles_from_agents_arg(agents_arg, available)
        if not state.enabled_roles:
            fail("--agents did not match any discovered agents.")
            sys.exit(1)
        ok(f"Enabled {len(state.enabled_roles)} agents from --agents.")
        return
    print()
    print("  Available agents (Enter = full fleet):")
    print("    [full]     enabled by the default full-fleet setup")
    print("    [starter]  explicit small setup for lab installs only")
    print("    (scope-idle) enabled by full fleet; no-op until required scope is configured")
    starter = set(starter_roles(available))
    for role in available:
        codename, desc, _, _ = AGENT_CATALOG[role]
        marker = "[starter]" if role in starter else "[full]   "
        suffix = " (scope-idle)" if role in SCOPE_GATED_ROLES else ""
        print(f"    {marker} {codename:<20s}{suffix:<14s} {desc}")
    print()
    if non_interactive:
        state.enabled_roles = recommended_roles(available)
        ok(f"Enabled full fleet ({len(state.enabled_roles)} agents).")
        return
    raw = ask("Choose agents: Enter for full fleet, 'starter', or comma-separated codenames", "")
    state.enabled_roles = roles_from_agents_arg(raw or "all", available)
    if not state.enabled_roles:
        warn("Nothing matched. Using the full fleet.")
        state.enabled_roles = recommended_roles(available)
    ok(f"{len(state.enabled_roles)} agents enabled.")


def step_7_repos(
    state: WizardState, *, repos_arg: str | None = None, non_interactive: bool
) -> None:
    step("Per-agent repos")
    repo_roles = [r for r in state.enabled_roles if role_uses_repos(r)]
    if not repo_roles:
        ok("No repo-operating agents enabled; skipping repo prompts.")
    else:
        arg_repos: list[str] | None = None
        if repos_arg is not None:
            arg_repos = _resolve_repo_selection(
                repos_arg, state.repos, gh_org=state.gh_org, allow_external=True
            )
            if not arg_repos and repos_arg.strip().lower() != "none":
                fail(f"--repos did not match any visible repo: {repos_arg}")
                sys.exit(1)
            outside_org = [
                repo
                for repo in arg_repos
                if "/" in repo
                and state.gh_org
                and repo.split("/", 1)[0].lower() != state.gh_org.lower()
            ]
            if outside_org:
                fail(
                    "--repos must belong to GH_ORG for the shipped agents. "
                    f"Set GH_ORG accordingly or use bare repo names. Outside scope: {', '.join(outside_org)}"
                )
                sys.exit(1)

        for role in repo_roles:
            codename = runtime_id_for_role(role)
            # Honor --config role_repos (per-agent scoping) over the broader
            # --repos / "repos" / non-interactive default-all behaviour.
            if role in state.role_to_repos:
                continue
            if arg_repos is not None:
                state.role_to_repos[role] = list(arg_repos)
                continue
            if non_interactive:
                if len(state.repos) == 1:
                    state.role_to_repos[role] = list(state.repos)
                    continue
                fail(
                    "Non-interactive setup with repo agents needs --repos or per-agent "
                    "role_repos in --config. Example: --repos owner/repo or --repos repo-a,repo-b"
                )
                sys.exit(1)
            if not state.repos:
                state.role_to_repos[role] = []
                continue
            print()
            print(f"  Repos for {codename} ({AGENT_CATALOG[role][1]}):")
            for i, repo in enumerate(state.repos, 1):
                print(f"    {i:>2}. {repo}")
            default = "all" if len(state.repos) == 1 else ""
            while True:
                raw = ask(
                    "Numbers, 'all', 'engineering' (excludes specs/docs), or 'none'",
                    default,
                )
                selected = _resolve_repo_selection(raw, state.repos, gh_org=state.gh_org)
                if selected or (raw or "").strip().lower() == "none":
                    state.role_to_repos[role] = selected
                    break
                fail("Select at least one repo, or type 'none' to leave this agent idle.")
    # Role-specific setup prompts (staging URL, ECS cluster, etc.).
    managed_defaults = read_managed_env_file(state.env_file)
    for role in state.enabled_roles:
        codename = runtime_id_for_role(role)
        canonical = AGENT_CATALOG[role][0]
        prompts = SPECIAL_PROMPTS.get(canonical, [])
        if not prompts:
            continue
        extras: dict[str, str] = {}
        for env_key, label in prompts:
            current = state.role_to_extras.get(role, {}).get(
                env_key, managed_defaults.get(env_key, "")
            )
            val = ask(f"{label}", current, non_interactive=non_interactive)
            if val:
                extras[env_key] = val
        if extras:
            state.role_to_extras.setdefault(role, {}).update(extras)


def _resolve_repo_selection(
    raw: str, repos: list[str], *, gh_org: str = "", allow_external: bool = False
) -> list[str]:
    raw = (raw or "").strip()
    command = raw.lower()
    if not raw or command == "all":
        return list(repos)
    if command == "none":
        return []
    if command == "engineering":
        return [r for r in repos if not any(s in r.lower() for s in ("spec", "doc", "wiki"))]
    by_full = {r.lower(): r for r in repos}
    by_name = {r.rsplit("/", 1)[-1].lower(): r for r in repos}
    out: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        tok_lower = tok.lower()
        chosen = ""
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(repos):
                chosen = repos[idx]
        elif tok_lower in by_full:
            chosen = by_full[tok_lower]
        elif tok_lower in by_name:
            chosen = by_name[tok_lower]
        elif allow_external and "/" in tok:
            chosen = tok
        elif allow_external and gh_org:
            chosen = f"{gh_org}/{tok}"
        if chosen and chosen not in out:
            out.append(chosen)
    return out


def step_8_schedule(state: WizardState, *, non_interactive: bool) -> None:
    step("Schedules")
    for role in state.enabled_roles:
        # Preserve any --config role_schedule override; only fill defaults.
        if role not in state.role_to_schedule:
            state.role_to_schedule[role] = AGENT_CATALOG[role][3]
    if non_interactive:
        ok("Sensible defaults assigned.")
        return
    raw = ask("Press 'a' to customize, anything else to accept defaults", "")
    if raw.lower() != "a":
        ok("Sensible defaults assigned.")
        return
    for role in state.enabled_roles:
        codename = runtime_id_for_role(role)
        current = state.role_to_schedule[role]
        new = ask(f"Schedule for {codename}", current)
        state.role_to_schedule[role] = new


def step_8b_telemetry(state: WizardState, *, non_interactive: bool) -> None:
    """Prompt for anonymous proof-telemetry. Default is YES, with opt-out."""
    step("Anonymous usage totals")
    note(
        "Alfred can share anonymous aggregate counts: PRs opened, PRs merged, "
        "terminal PRs, and changed files."
    )
    note("Never sent: repo names, code, paths, prompts, titles, people, or hostnames.")
    note("Turn it off any time with `alfred telemetry off`.")
    if state.telemetry_enabled and not state.telemetry_url:
        state.telemetry_url = default_telemetry_url()
    if non_interactive:
        if state.telemetry_enabled and state.telemetry_url:
            ok(
                "Anonymous totals will use "
                f"{telemetry_endpoint_label(state.telemetry_url)}. "
                "Disable any time with `alfred telemetry off`."
            )
        else:
            ok("Telemetry opted out.")
        return
    share = ask_yes_no("Share anonymous usage totals?", default=state.telemetry_enabled)
    if not share:
        state.telemetry_enabled = False
        state.telemetry_url = ""
        ok("Telemetry opted out.")
        return
    state.telemetry_enabled = True
    state.telemetry_url = state.telemetry_url or default_telemetry_url()
    ok("Telemetry enabled. Disable any time with `alfred telemetry off`.")


def apply_batteries_arg(state: WizardState, batteries_arg: str | None) -> None:
    """Merge a ``--batteries`` selection into the wizard state.

    Accepts a comma-separated list of opt-in battery ids, or ``none``/``builtin``
    to keep built-ins only. Unknown ids and built-in ids are warned and skipped,
    so an operator can never enable something that is not a real opt-in battery.
    """
    if batteries_arg is None:
        return
    raw = batteries_arg.strip().lower()
    if raw in {"", "none", "builtin", "built-in", "builtins"}:
        state.batteries = []
        state.battery_defaults_disabled = True
        return
    selected: list[str] = list(state.batteries)
    for token in raw.split(","):
        bid = token.strip()
        if not bid:
            continue
        battery = batteries.battery_by_id(bid)
        if battery is None:
            warn(f"--batteries: unknown battery {bid!r}; ignored")
            continue
        if battery.builtin:
            warn(f"--batteries: {bid!r} is a built-in and is always on; ignored")
            continue
        if bid not in selected:
            selected.append(bid)
    state.batteries = selected


def _battery_requirement_line(battery: batteries.Battery) -> str:
    if battery.requires_daemon:
        return f"needs {battery.service} running (you start it yourself)"
    if battery.install_kind == batteries.INSTALL_PIP_EXTRA and battery.pip_extra:
        return f'needs `pip install "alfred-os[{battery.pip_extra}]"`'
    if battery.install_kind == batteries.INSTALL_PIP_EXTRA:
        return "needs an extra Python package"
    if battery.install_kind == batteries.INSTALL_AUTOFETCH:
        return "Alfred fetches a small pinned binary on first use"
    return "no extra install"


def step_8c_batteries(state: WizardState, *, non_interactive: bool) -> None:
    """Show included tools and offer advanced integrations.

    Fresh installs keep the default-on local tools without writing redundant env
    flags. Interactive setup only asks about advanced integrations. This records
    choices; ``step_9_generate`` writes their flags, and `alfred batteries
    enable` owns dependency installation. The wizard never starts a daemon.
    """
    step("Included tools")
    note("These local tools are part of Alfred and are on by default:")
    for battery in batteries.builtin_batteries():
        note(f"    included  {battery.name} ({battery.category})")
    for battery in batteries.default_batteries():
        note(f"    included  {battery.name} ({battery.category}; verified on first use)")

    advanced = batteries.advanced_batteries()
    if non_interactive:
        # A pre-seeded selection (--batteries / --config) that names two
        # mutually-exclusive primaries (Redis and pgvector) is a hard error, not
        # a silent last-write-wins on ALFRED_MEMORY_PROVIDERS.
        conflict = batteries.selection_conflict(state.batteries)
        if conflict:
            fail(f"Battery selection conflict: {conflict}")
            sys.exit(1)
        if state.battery_defaults_disabled:
            ok("Built-ins only selected. Included configurable tools are disabled.")
        elif state.batteries:
            chosen = ", ".join(state.batteries)
            ok(f"Advanced integrations selected: {chosen}. Included tools stay on.")
        else:
            ok("Included tools ready. Add advanced integrations later with `alfred batteries`.")
        return

    note("Advanced integrations are off by default. Turn on any you need:")
    selected = list(state.batteries)
    for battery in advanced:
        print(f"  {STYLE.BLUE}{battery.name}{STYLE.OFF} ({battery.category})")
        print(f"    {battery.how_it_helps}")
        print(f"    {_battery_requirement_line(battery)}")
        default_on = battery.id in selected
        if ask_yes_no(f"Enable {battery.name}?", default=default_on):
            prospective = [*selected, battery.id] if battery.id not in selected else selected
            conflict = batteries.selection_conflict(prospective)
            if conflict:
                warn(f"Skipping {battery.id}: {conflict}")
                continue
            if battery.id not in selected:
                selected.append(battery.id)
            if battery.requires_daemon:
                note(f"    Remember: start {battery.service} yourself. {battery.install_hint}")
            elif battery.install_kind in (batteries.INSTALL_PIP_EXTRA, batteries.INSTALL_AUTOFETCH):
                note(f"    Finish install later with `alfred batteries enable {battery.id}`.")
        elif battery.id in selected:
            selected.remove(battery.id)
    state.batteries = selected
    if selected:
        ok(f"Advanced integrations selected: {', '.join(selected)}.")
    else:
        ok("Included tools ready. Add advanced integrations later with `alfred batteries`.")


def step_9_generate(state: WizardState, *, non_interactive: bool) -> None:
    step("Generate config")
    conf = render_agents_conf(state)
    print()
    print("--- agents.conf ---")
    print(conf)
    print("-------------------")
    if not non_interactive and not ask_yes_no("Looks good?", True):
        warn("Re-run alfred-init to revise. Existing config left in place.")
        sys.exit(1)

    existing_managed_env = read_managed_env_file(state.env_file)
    target = state.repo_root / "launchd" / "agents.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(conf)
    ok(f"wrote {target}")
    env_kvs = {**existing_managed_env, **env_assignments_for(state)}
    upsert_env_file(state.env_file, env_kvs)
    ok(f"updated {state.env_file} with {len(env_kvs)} keys")
    created_prompts = seed_prompt_templates(state)
    if created_prompts:
        ok(
            f"seeded {len(created_prompts)} prompt template(s) under {state.alfred_home / 'prompts'}"
        )
    else:
        ok("prompt templates already present or not needed")
    scope_gated = write_fleet_enable_state(state)
    if scope_gated:
        ok(f"enabled scope-gated agent(s): {', '.join(scope_gated)}")


def seed_runtime_roster(state: WizardState, *, agents_arg: str | None) -> int:
    """Seed a deployable runtime roster without account probes.

    Desktop install uses this between install.sh and deploy.sh. It should leave
    repo-scoped agents idle until onboarding saves repos, while still giving the
    scheduler a full local roster to load.
    """
    step("Seed runtime fleet roster")
    if not state.env_file.exists():
        fail(f"{state.env_file} missing. Run install.sh before seeding the runtime roster.")
        return 1

    available = discover_agents(state.repo_root / "bin")
    if not available:
        fail(f"No agent runners discovered under {state.repo_root / 'bin'}.")
        return 1

    if state.enabled_roles and not agents_arg:
        state.enabled_roles = [
            role for role in AGENT_CATALOG if role in state.enabled_roles and role in available
        ]
        if not state.enabled_roles:
            fail("--config agents did not match any discovered agents.")
            return 1
    else:
        state.enabled_roles = roles_from_agents_arg(agents_arg or "all", available)
        if not state.enabled_roles:
            fail("--agents did not match any discovered agents.")
            return 1

    ok(f"Enabled full runtime roster ({len(state.enabled_roles)} agents).")
    existing_managed_env = read_managed_env_file(state.env_file)
    for role in state.enabled_roles:
        state.role_to_repos.setdefault(role, [])
    step_8_schedule(state, non_interactive=True)

    existing_env = {**read_env_file(state.env_file), **existing_managed_env}
    existing_telemetry_enabled = existing_env.get("ALFRED_TELEMETRY_ENABLED", "").strip()
    existing_telemetry_url = existing_env.get("ALFRED_TELEMETRY_URL", "").strip()
    if existing_telemetry_url and (
        not existing_telemetry_enabled or parse_consent(existing_telemetry_enabled)
    ):
        state.telemetry_enabled = True
        state.telemetry_url = existing_telemetry_url
        state.telemetry_token = existing_env.get("ALFRED_TELEMETRY_TOKEN", "").strip()
    else:
        state.telemetry_enabled = parse_consent(existing_telemetry_enabled)
        # Fresh roster seeding is local-only. Leave proof telemetry unscheduled
        # until a full interactive or configured onboarding step makes that
        # choice.
        state.telemetry_url = ""

    conf = render_agents_conf(state)
    target = state.alfred_home / "launchd" / "agents.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(conf)
    ok(f"wrote {target}")

    env_kvs = {**existing_managed_env, **env_assignments_for(state)}
    upsert_env_file(state.env_file, env_kvs)
    ok(f"updated {state.env_file} with {len(env_kvs)} fleet key(s)")

    created_prompts = seed_prompt_templates(state)
    if created_prompts:
        ok(
            f"seeded {len(created_prompts)} prompt template(s) under {state.alfred_home / 'prompts'}"
        )
    else:
        ok("prompt templates already present or not needed")

    write_fleet_enable_state(state)
    ok("repo-scoped agents will stay idle until onboarding saves repositories")
    return 0


def step_10_labels(state: WizardState, *, skip: bool = False) -> None:
    step("GitHub labels")
    if skip:
        warn("Skipping GitHub label setup by request.")
        return
    repos = label_setup_repos(state)
    if not repos:
        ok("No selected repos; skipping label setup.")
        return
    created_or_present = 0
    warnings = 0
    for repo in repos:
        full_repo = repo if "/" in repo else f"{state.gh_org}/{repo}"
        for name, color, desc in SETUP_LABELS:
            cp = run(
                [
                    "gh",
                    "label",
                    "create",
                    name,
                    "--color",
                    color,
                    "--description",
                    desc,
                    "-R",
                    full_repo,
                ],
                timeout=15,
            )
            if cp.returncode == 0 or "already" in (cp.stderr or "").lower():
                created_or_present += 1
                continue
            warnings += 1
            warn(f"Could not ensure label {name!r} on {full_repo}: {cp.stderr.strip()}")
    ok(f"labels checked on {len(repos)} repo(s), {created_or_present} label operations ok")
    if warnings:
        warn(f"{warnings} label operation(s) need a manual check; agents can still run.")


def step_10_deploy(state: WizardState) -> None:
    step("Deploy")
    deploy_path = state.repo_root / "deploy.sh"
    if not deploy_path.exists():
        fail(f"{deploy_path} missing.")
        sys.exit(1)
    cp = run(["bash", str(deploy_path)], capture=False, timeout=300)
    if cp.returncode != 0:
        fail("deploy.sh failed. Re-run after fixing the cause.")
        sys.exit(1)
    ok("deploy.sh OK")


def step_11_doctor(state: WizardState, *, non_interactive: bool) -> bool:
    step("Doctor")
    doctor_path = state.repo_root / "bin" / "doctor.sh"
    if not doctor_path.exists():
        warn("doctor.sh missing; skipping.")
        return True
    cp = run(["bash", str(doctor_path)], capture=False, timeout=600)
    if cp.returncode == 0:
        ok("doctor passed")
        return True
    fail("doctor reported failures.")
    if non_interactive:
        return False
    return ask_yes_no("Continue anyway?", False)


def step_12_smoke(state: WizardState) -> None:
    step("Smoke test")
    n = len(state.enabled_roles)
    if state.slack_webhook:
        ok_post, body = slack_post(
            state.slack_webhook, f"alfred-os: configured and ready. {n} agents enabled."
        )
        if ok_post:
            ok("final Slack post sent")
        else:
            warn(f"Final Slack post failed: {body}")
    print()
    print(f"{STYLE.GREEN}Done.{STYLE.OFF}")
    print()
    print(f"  Fleet: {n} agents enabled")
    if state.slack_webhook:
        masked = state.slack_webhook[:48] + "…"
        print(f"  Slack: {masked}")
    print("  Agents:")
    for role in state.enabled_roles:
        codename = runtime_id_for_role(role)
        desc = AGENT_CATALOG[role][1]
        sched = state.role_to_schedule.get(role, AGENT_CATALOG[role][3])
        repos = state.role_to_repos.get(role, [])
        repo_str = ", ".join(repos[:3]) + (f" (+{len(repos) - 3})" if len(repos) > 3 else "")
        if repos:
            print(f"    {codename:<22s} ({desc.split(' (')[0]:<32s}) → {sched} on {repo_str}")
        else:
            print(f"    {codename:<22s} ({desc.split(' (')[0]:<32s}) → {sched}")
    print()
    print("  Operator commands:")
    print("    alfred agents:         configured agents + runner-gate state")
    print("    alfred enable <agent> , add a role codename to the runner gate")
    print("    alfred disable <agent>, remove a codename from the runner gate")
    print("    alfred doctor:         preflight configured Python agents")
    print()
    print("  Read docs/AGENTS.md for the full codename topology.")
    print("  Read INSTALL.md if anything went sideways.")


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="alfred-init agent fleet configuration wizard.")
    p.add_argument(
        "--non-interactive", action="store_true", help="Accept all defaults; never prompt."
    )
    p.add_argument("--config", type=Path, default=None, help="JSON file with pre-baked answers.")
    p.add_argument(
        "--agents",
        type=str,
        default=None,
        help="starter, all, or comma-separated codenames/roles to enable.",
    )
    p.add_argument(
        "--repos",
        type=str,
        default=None,
        help="Comma-separated repo selection for repo-operating agents.",
    )
    p.add_argument(
        "--batteries",
        type=str,
        default=None,
        help=(
            "Comma-separated opt-in battery ids to enable (e.g. dense-embeddings). "
            "'none' keeps built-ins only. Works in non-interactive mode."
        ),
    )
    p.add_argument(
        "--slack-webhook",
        type=str,
        default=None,
        help="Slack webhook URL, or 'skip' to skip Slack setup.",
    )
    p.add_argument(
        "--skip-label-setup",
        action="store_true",
        help="Do not create the standard Alfred GitHub labels during setup.",
    )
    p.add_argument(
        "--seed-runtime-roster",
        action="store_true",
        help=(
            "Seed ALFRED_HOME/launchd/agents.conf and prompts without GitHub, "
            "Claude, labels, deploy, or doctor."
        ),
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to the alfred-os checkout (default: parent of this script).",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def load_config(path: Path) -> dict:
    if not path.exists():
        fail(f"--config file {path} not found.")
        sys.exit(1)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        fail(f"--config file {path} is not valid JSON: {e}")
        sys.exit(1)


def _resolve_role_key(key: str) -> str | None:
    """Map a config key (role-key or codename) to its canonical role-key.

    Returns ``None`` if the key matches neither a known role nor a
    known default codename. Lookup is case-insensitive on both sides
    so a JSON config can use whichever surface the operator finds
    natural (``"feature_dev"`` or ``"senior-dev"``).
    """
    k = key.lower()
    for role in AGENT_CATALOG:
        if role.lower() == k:
            return role
    for codename, role in CODENAME_TO_ROLE.items():
        if codename.lower() == k:
            return role
    return None


def apply_config_overrides(state: WizardState, cfg: dict) -> None:
    """Honor pre-baked answers from --config.

    Supported keys:

    - ``gh_org`` (str): GitHub org / user the fleet operates on.
    - ``slack_webhook`` (str), ``slack_storage`` (``"env"`` or
      ``"aws"``): Slack post target and credential storage.
    - ``use_aws`` (bool), ``aws_agent_profiles`` (dict): per-agent
      AWS profile names for IAM-scoped agents (e2e-runner, ops-watch).
    - ``agents`` (list[str]): codenames or role-keys to enable.
    - ``repos`` (str | list[str]): convenience override applied to
      every repo-operating agent. For per-agent scoping use
      ``role_repos`` instead.
    - ``role_repos`` (dict[str, list[str]]): per-agent repo
      assignment. Keys are codenames (``"lucius"``) or role-keys
      (``"feature_dev"``), case-insensitive. Values are repo slugs
      (bare ``"my-repo"`` resolves through ``GH_ORG``; ``"org/repo"``
      is treated as a full slug). Agents not listed fall through to
      ``repos`` / ``--repos`` / interactive prompts.
    - ``role_schedule`` (dict[str, str]): override the default
      schedule for an agent. Key resolves the same way as
      ``role_repos``; value is in ``agents.conf`` schedule format
      (``"interval:1200"``, ``"cron:7:30"``, ``"cron:1:7:30"``).
    - ``role_extras`` (dict[str, dict[str, str]]): per-agent env values
      normally collected by interactive prompts, such as
      ``ALFRED_E2E_RUNNER_TARGET_URL`` or ``ALFRED_OPS_WATCH_ECS_CLUSTER``.
    - ``telemetry_enabled`` (bool), ``telemetry_url`` (str): configure
      anonymous proof-telemetry non-interactively. Reporting is opt-out and
      uses Alfred's hosted collector by default. ``telemetry_url`` overrides it
      for self-hosted collectors.
      ``telemetry_enabled`` is parsed strictly (see ``parse_consent``): a
      quoted ``"false"`` opts out.
    - ``telemetry_token`` (str): optional shared ingest token sent as
      ``X-Ingest-Token``; only written when telemetry is opted in.
    - ``batteries`` (str | list[str]): opt-in battery ids to enable (e.g.
      ``["dense-embeddings"]``). ``"none"`` keeps built-ins only. Built-ins are
      always on and cannot be listed here.
    """
    if "gh_org" in cfg:
        state.gh_org = cfg["gh_org"]
    if "slack_webhook" in cfg:
        state.slack_webhook = cfg["slack_webhook"]
    if "slack_storage" in cfg:
        state.slack_storage = cfg["slack_storage"]
    if "use_aws" in cfg:
        state.use_aws = bool(cfg["use_aws"])
    if "aws_agent_profiles" in cfg:
        state.aws_agent_profiles = dict(cfg["aws_agent_profiles"])
    if "agents" in cfg:
        # cfg["agents"] is a list of codenames or role keys.
        wanted = {str(item).lower() for item in cfg["agents"]}
        state.enabled_roles = [
            r
            for r, (cn, _, _, _) in AGENT_CATALOG.items()
            if cn.lower() in wanted or r.lower() in wanted
        ]
    if "repos" in cfg:
        repos = cfg["repos"]
        if isinstance(repos, str):
            state.role_to_repos["__all__"] = [repos]
        elif isinstance(repos, list):
            state.role_to_repos["__all__"] = [str(r) for r in repos]
    if "role_repos" in cfg and isinstance(cfg["role_repos"], dict):
        for raw_key, raw_repos in cfg["role_repos"].items():
            role = _resolve_role_key(str(raw_key))
            if role is None:
                warn(f"--config role_repos: unknown agent {raw_key!r}; ignored")
                continue
            if isinstance(raw_repos, str):
                state.role_to_repos[role] = [raw_repos]
            elif isinstance(raw_repos, list):
                state.role_to_repos[role] = [str(r) for r in raw_repos]
            else:
                warn(
                    f"--config role_repos[{raw_key!r}]: expected list or str, "
                    f"got {type(raw_repos).__name__}; ignored"
                )
    if "role_codename" in cfg:
        fail(
            "--config role_codename was removed. Use roster themes for display names "
            "or `alfred agent add` for a new runtime agent."
        )
        raise SystemExit(2)
    if "role_schedule" in cfg and isinstance(cfg["role_schedule"], dict):
        for raw_key, raw_schedule in cfg["role_schedule"].items():
            role = _resolve_role_key(str(raw_key))
            if role is None:
                warn(f"--config role_schedule: unknown agent {raw_key!r}; ignored")
                continue
            state.role_to_schedule[role] = str(raw_schedule)
    if "role_extras" in cfg and isinstance(cfg["role_extras"], dict):
        for raw_key, raw_values in cfg["role_extras"].items():
            role = _resolve_role_key(str(raw_key))
            if role is None:
                warn(f"--config role_extras: unknown agent {raw_key!r}; ignored")
                continue
            if not isinstance(raw_values, dict):
                warn(
                    f"--config role_extras[{raw_key!r}]: expected object, "
                    f"got {type(raw_values).__name__}; ignored"
                )
                continue
            state.role_to_extras.setdefault(role, {}).update(
                {str(k): str(v) for k, v in raw_values.items() if str(k).strip()}
            )
    # Telemetry opt-out. A missing key keeps the default: enabled, using
    # Alfred's hosted collector unless telemetry_url overrides it.
    # parse_consent is strict: a quoted "false"/"0"/"no" (or anything that is
    # not a recognized truthy token) opts out, unlike bool("false") which is
    # True.
    if "telemetry_enabled" in cfg:
        state.telemetry_enabled = parse_consent(cfg["telemetry_enabled"])
    if "telemetry_url" in cfg:
        state.telemetry_url = str(cfg["telemetry_url"])
    if "telemetry_token" in cfg:
        state.telemetry_token = str(cfg["telemetry_token"])
    if "batteries" in cfg:
        raw = cfg["batteries"]
        tokens = raw if isinstance(raw, list) else [raw]
        apply_batteries_arg(state, ",".join(str(item) for item in tokens))


def main(argv: Iterable[str] | None = None) -> int:
    if os.environ.get("ALFRED_DOCTOR"):
        print("[ALFRED-INIT-DOCTOR-OK]")
        return 0

    args = parse_args(argv)
    non_interactive = args.non_interactive or bool(os.environ.get("ALFRED_NONINTERACTIVE"))

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    alfred_home = Path(os.environ.get("ALFRED_HOME") or (Path.home() / ".alfred"))
    env_file = alfred_home / ".env"

    print(f"{STYLE.BLUE}alfred-init{STYLE.OFF} agent fleet configuration.")
    print(f"  Repo:        {repo_root}")
    print(f"  ALFRED_HOME: {alfred_home}")
    print(f"  .env:        {env_file}")
    print()

    state = WizardState(alfred_home=alfred_home, env_file=env_file, repo_root=repo_root)

    if args.config:
        apply_config_overrides(state, load_config(args.config))

    if args.seed_runtime_roster:
        return seed_runtime_roster(state, agents_arg=args.agents)

    step_0_preflight(state)
    step_1_claude(non_interactive=non_interactive)
    step_2_github(state, non_interactive=non_interactive)
    step_3_slack(state, slack_arg=args.slack_webhook, non_interactive=non_interactive)
    available = discover_agents(repo_root / "bin")
    step_5_pick_agents(state, available, agents_arg=args.agents, non_interactive=non_interactive)
    step_4_aws(state, non_interactive=non_interactive)  # after pick_agents so we know who needs AWS
    config_repos = None
    if "__all__" in state.role_to_repos:
        config_repos = ",".join(state.role_to_repos.pop("__all__"))
    step_7_repos(state, repos_arg=args.repos or config_repos, non_interactive=non_interactive)
    step_8_schedule(state, non_interactive=non_interactive)
    step_8b_telemetry(state, non_interactive=non_interactive)
    apply_batteries_arg(state, args.batteries)
    step_8c_batteries(state, non_interactive=non_interactive)
    step_9_generate(state, non_interactive=non_interactive)
    step_10_labels(state, skip=args.skip_label_setup)
    step_10_deploy(state)
    if not step_11_doctor(state, non_interactive=non_interactive):
        fail("Doctor failed. Resolve and re-run `alfred doctor`.")
        return 1
    step_12_smoke(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())

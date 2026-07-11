#!/usr/bin/env python3
"""``fleet-doctor``, daily fleet-health snapshot agent.

Read-only health checks across the on-disk state files. Posts a single
Slack thread (Block Kit when a bot token is configured, webhook
fallback otherwise) summarising findings as green / yellow / red.

Checks (each is a small pure function returning a ``Finding`` tuple so
unit tests can target it in isolation):

1. ``check_paused_repos``:    ``$ALFRED_HOME/state/paused-repos.json``;
                                yellow if any repo is paused.
2. ``check_global_block``:    fleet-wide rate-limit poison pill;
                                red when active.
3. ``check_stale_worktrees`` , ``$ALFRED_HOME/worktrees/`` entries
                                with mtime >24h ago (heuristic for
                                stuck firings).
4. ``check_enabled_agents``:  ``$ALFRED_HOME/state/fleet/enabled.txt``
                                contents; surfaces the configured fleet
                                so the operator sees the gating state.
5. ``check_paused_agents``:   pause markers under
                                ``$ALFRED_HOME/state/_paused``.
6. ``check_spend_state``:     today's spend and failure-streak files.

The checks use only local state already written by alfred-os. Port operators
can extend with network checks (OAuth expiry, queue depth, deploy drift)
without changing the ``Finding`` contract.

Health snapshot runner for local fleets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from agent_runner import (  # noqa: E402
    STATE_ROOT,
    WORKTREE_ROOT,
    PreflightSpec,
    disk_pressure_status,
    doctor_mode,
    engine_quota_backoff,
    is_globally_blocked,
    list_enabled_agents,
    list_paused_repos,
    preflight,
    slack_post,
    with_lock,
)
from agent_runner.config import agent_engine  # noqa: E402
from agent_runner.paths import config_value  # noqa: E402
from slack.posting import firing_thread_root  # noqa: E402

AGENT = "fleet-doctor"

STALE_WORKTREE_SECONDS = 24 * 3600

# Severity rank for picking the post-level severity (worst wins).
SEVERITY_RANK = {"green": 0, "yellow": 1, "alert": 2}
SEVERITY_TO_SLACK = {"green": "info", "yellow": "warn", "alert": "alert"}


@dataclass
class Finding:
    name: str
    severity: str  # "green" | "yellow" | "alert"
    message: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.name, self.severity, self.message)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_paused_repos() -> Finding:
    """Yellow when any repo is paused; green otherwise."""
    paused = list_paused_repos()
    if not paused:
        return Finding("paused-repos", "green", "no repos paused")
    return Finding("paused-repos", "yellow", f"{len(paused)} repo(s) paused: {', '.join(paused)}")


def check_global_block() -> Finding:
    """Red when a fleet-wide rate-limit block is active."""
    blocked = is_globally_blocked()
    if not blocked:
        return Finding("global-block", "green", "no fleet-wide block")
    return Finding("global-block", "alert", f"fleet-wide block active: {blocked}")


def check_stale_worktrees() -> Finding:
    """Yellow when worktrees exist that haven't been touched in 24h+."""
    if not WORKTREE_ROOT.exists():
        return Finding("stale-worktrees", "green", "no worktrees")
    import time

    now = time.time()
    stale: list[Path] = []
    for p in WORKTREE_ROOT.iterdir():
        if not p.is_dir():
            continue
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age > STALE_WORKTREE_SECONDS:
            stale.append(p)
    if not stale:
        return Finding("stale-worktrees", "green", "no stale worktrees")
    sample = ", ".join(p.name for p in stale[:3])
    return Finding(
        "stale-worktrees",
        "yellow",
        f"{len(stale)} stale worktree(s) (>{STALE_WORKTREE_SECONDS // 3600}h): {sample}"
        + ("…" if len(stale) > 3 else ""),
    )


def check_disk_pressure() -> Finding:
    """Free space on the filesystem holding ALFRED_HOME.

    Green when healthy, yellow on a ``low`` early-warning reading, red
    (alert) when ``critical`` - the same thresholds the preflight gate
    uses to skip firings, so ``alfred doctor`` shows the operator exactly
    why agents are backing off before the channel fills with skip
    warnings.
    """
    status = disk_pressure_status()
    detail = f"{status['free_gb']:.1f}GB free ({status['free_pct']:.1f}%)"
    if status["critical"]:
        return Finding(
            "disk-pressure",
            "alert",
            f"🔴 disk critically low: {detail}. Agents skip firings to avoid "
            "ENOSPC. Run `agent-cleanup.py --emergency` or free space.",
        )
    if status["low"]:
        return Finding(
            "disk-pressure",
            "yellow",
            f"disk getting low: {detail} (approaching the back-off threshold).",
        )
    return Finding("disk-pressure", "green", f"disk healthy: {detail}")


def check_enabled_agents() -> Finding:
    """Surface the configured runner gate list. Always green, purely
    informational so the operator can confirm the gating state."""
    if not (STATE_ROOT / "fleet" / "enabled.txt").exists():
        return Finding(
            "enabled-agents",
            "green",
            "fleet gate file missing → runners fall back to their own defaults",
        )
    enabled = list_enabled_agents()
    if not enabled:
        return Finding("enabled-agents", "yellow", "fleet gate file present but empty")
    return Finding(
        "enabled-agents",
        "green",
        f"{len(enabled)} agent(s) listed in runner gate: {', '.join(enabled)}",
    )


def check_paused_agents() -> Finding:
    pause_dir = STATE_ROOT / "_paused"
    if not pause_dir.is_dir():
        return Finding("paused-agents", "green", "no paused agents")
    markers = sorted(path for path in pause_dir.iterdir() if path.is_file())
    if not markers:
        return Finding("paused-agents", "green", "no paused agents")

    import time

    now = time.time()
    parts: list[str] = []
    old = 0
    for marker in markers[:8]:
        try:
            hours = int((now - marker.stat().st_mtime) // 3600)
        except OSError:
            hours = 0
        if hours >= 24:
            old += 1
        parts.append(f"{marker.name} ({hours}h)")
    suffix = " (some >24h)" if old else ""
    more = f", +{len(markers) - 8} more" if len(markers) > 8 else ""
    return Finding("paused-agents", "yellow", f"Paused agents{suffix}: {', '.join(parts)}{more}")


def _today_spend_files() -> list[Path]:
    # SpendState writes under a UTC day key (`agent_runner/spend.today_str()`);
    # match here so non-UTC hosts during local/UTC date-skew windows don't
    # report `no spend today` while writes are still landing on the UTC day.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return sorted(STATE_ROOT.glob(f"*/spend-{today}.json"))


def check_spend_state() -> Finding:
    files = [path for path in _today_spend_files() if not path.parent.name.startswith("_")]
    if not files:
        return Finding("spend-state", "green", "no spend files for today yet")

    yellow: list[str] = []
    alerts: list[str] = []
    for path in files:
        agent = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            yellow.append(f"{agent}: unreadable spend file")
            continue
        consecutive = int(data.get("consecutive_failures") or 0)
        failures = int(data.get("failures_today") or 0)
        successes = int(data.get("successes_today") or 0)
        blocked_until = str(data.get("blocked_until") or "").strip()
        if consecutive >= 8:
            alerts.append(f"{agent}: {consecutive} consecutive failures")
        elif consecutive or failures:
            yellow.append(f"{agent}: {failures} fail / {successes} ok")
        if blocked_until:
            try:
                parsed = datetime.fromisoformat(blocked_until.replace("Z", "+00:00"))
                if parsed.astimezone(UTC) > datetime.now(UTC):
                    yellow.append(f"{agent}: blocked until {blocked_until}")
            except ValueError:
                yellow.append(f"{agent}: invalid blocked_until={blocked_until}")

    if alerts:
        return Finding("spend-state", "alert", "; ".join(alerts[:6]))
    if yellow:
        return Finding("spend-state", "yellow", "; ".join(yellow[:6]))
    return Finding("spend-state", "green", f"{len(files)} spend file(s), no failure streaks")


ENGINE_AUTH_WINDOW_SECONDS = 3600  # last 1h
ENGINE_AUTH_MIN_AGENTS = 3


def _recent_event_jsonl_paths(
    *,
    window_seconds: int = ENGINE_AUTH_WINDOW_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """Return event-log JSONL files modified within ``window_seconds``."""
    import time as _time

    now_ts = _time.time() if now is None else now
    cutoff = now_ts - window_seconds
    paths: list[Path] = []
    if not STATE_ROOT.is_dir():
        return paths
    for agent_dir in STATE_ROOT.iterdir():
        if not agent_dir.is_dir():
            continue
        events_dir = agent_dir / "events"
        if not events_dir.is_dir():
            continue
        for f in events_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime >= cutoff:
                    paths.append(f)
            except OSError:
                continue
    return paths


def _file_has_engine_auth_failure(path: Path, *, cutoff_ts: float) -> bool:
    """Return True iff ``path`` contains at least one event with
    ``subtype: error_authentication`` AND ``engine: claude`` within
    the window. Best-effort parser: malformed records are skipped."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                ts_str = rec.get("ts", "")
                if isinstance(ts_str, str) and ts_str:
                    try:
                        rec_ts = (
                            datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                            .replace(tzinfo=UTC)
                            .timestamp()
                        )
                        if rec_ts < cutoff_ts:
                            continue
                    except ValueError:
                        pass
                if rec.get("subtype") == "error_authentication" and rec.get("engine") == "claude":
                    return True
    except OSError:
        return False
    return False


def check_engine_auth_streak(
    *,
    window_seconds: int = ENGINE_AUTH_WINDOW_SECONDS,
    min_agents: int = ENGINE_AUTH_MIN_AGENTS,
    now: float | None = None,
) -> Finding:
    """Concurrent Anthropic auth failures across the fleet.

    Walks per-firing event JSONL files in the last ``window_seconds``
    and counts distinct agents emitting ``subtype: error_authentication``
    with ``engine: claude``. Red when ``min_agents`` or more concurrent
    agents hit the same failure mode within the window - the root cause
    is the operator's Anthropic session or Keychain ACL, not any
    individual agent's prompt.
    """
    import time as _time

    now_ts = _time.time() if now is None else now
    cutoff_ts = now_ts - window_seconds
    affected: set[str] = set()
    for path in _recent_event_jsonl_paths(window_seconds=window_seconds, now=now_ts):
        if _file_has_engine_auth_failure(path, cutoff_ts=cutoff_ts):
            agent = path.parent.parent.name
            affected.add(agent)
    if len(affected) >= min_agents:
        listed = ", ".join(sorted(affected))
        return Finding(
            "engine-auth-streak",
            "alert",
            (
                f"🔴 Engine auth failing: {len(affected)} agents hitting "
                f"error_authentication on engine=claude in last "
                f"{window_seconds // 60}m ({listed}). Likely Keychain ACL "
                "or session expiry. Run `alfred claude probe` to diagnose."
            ),
        )
    return Finding(
        "engine-auth-streak",
        "green",
        "No concurrent Anthropic auth failures.",
    )


# ---------------------------------------------------------------------------
# Credential + engine-quota + listener checks (the last-24h incident set)
# ---------------------------------------------------------------------------


def _configured_engine_agents() -> list[str]:
    """Best-effort list of agent codenames the fleet is configured to run.

    Prefers the runner gate (``list_enabled_agents``); falls back to the
    agents.conf roster so a host that has not written a gate file still gets
    checked. Empty list means "cannot determine" -> the caller degrades to a
    fleet-wide default-engine assumption rather than skipping the check.
    """
    try:
        enabled = list_enabled_agents()
    except Exception:
        enabled = []
    if enabled:
        return enabled
    codenames: list[str] = []
    for conf in (
        Path(os.environ.get("ALFRED_HOME", "")) / "launchd" / "agents.conf",
        _HERE.parent / "launchd" / "agents.conf",
    ):
        try:
            text = conf.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            label = fields[0].strip()
            if label:
                codenames.append(label.rsplit(".", 1)[-1])
        if codenames:
            break
    return codenames


def check_claude_credential(
    *,
    engine_resolver: Callable[[str], str] = agent_engine,
    credential_reader: Callable[[str], str] = config_value,
    agents: Sequence[str] | None = None,
) -> Finding:
    """Warn when a claude/hybrid agent has no reachable OAuth credential.

    This is the silent-401 outage class: an agent resolves to engine
    ``claude`` or ``hybrid`` but ``CLAUDE_CODE_OAUTH_TOKEN`` lives only in the
    macOS Keychain or a shell rc file the scheduler never sources, so every
    firing 401s and (in hybrid) falls back to codex until codex itself is
    exhausted. We resolve the token the exact way the runtime does (process
    env, then ``$ALFRED_HOME/.env``); a token only reachable interactively is
    invisible here, which is the point.
    """
    roster = list(agents) if agents is not None else _configured_engine_agents()
    # No roster resolvable -> assume the fleet default (hybrid) is in play, so
    # the credential still matters. Use a synthetic agent to force resolution.
    probe_agents = roster or ["_fleet_default"]
    needs_claude = [a for a in probe_agents if engine_resolver(a) in ("claude", "hybrid")]
    if not needs_claude:
        return Finding(
            "claude-credential",
            "green",
            "no claude/hybrid agents configured; OAuth token not required",
        )
    token = (credential_reader("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return Finding(
            "claude-credential",
            "green",
            f"CLAUDE_CODE_OAUTH_TOKEN reachable for {len(needs_claude)} claude/hybrid agent(s)",
        )
    return Finding(
        "claude-credential",
        "alert",
        (
            f"🔴 {len(needs_claude)} claude/hybrid agent(s) but CLAUDE_CODE_OAUTH_TOKEN is not "
            "reachable from env or $ALFRED_HOME/.env. Scheduled firings will 401 silently. "
            "Run `alfred setup-token`, then `alfred doctor --deep` to verify a headless probe."
        ),
    )


def check_engine_quota_backoff(
    *,
    backoff_reader: Callable[[str], dict[str, str] | None] = engine_quota_backoff,
    engines: Sequence[str] = ("claude", "codex"),
) -> Finding:
    """Surface any engine currently parked by a quota-exhaustion backoff.

    When the last real invocation of an engine hit a hard "usage limit ...
    try again at <date>" wall, the runner records a backoff so the scheduler
    skips that engine until its plan window resets. Show it here so the
    operator understands why an engine looks idle (yellow, not red: this is
    the system correctly protecting itself, not a fault).
    """
    parked: list[str] = []
    for engine in engines:
        record = backoff_reader(engine)
        if record:
            parked.append(f"{engine} until {record.get('until', '?')}")
    if not parked:
        return Finding("engine-quota", "green", "no engines parked on a usage-limit wall")
    return Finding(
        "engine-quota",
        "yellow",
        "engine(s) parked until plan reset: " + "; ".join(parked),
    )


AWS_STS_TIMEOUT_SECONDS = 10
WEBHOOK_CACHE_STALE_HOURS = 48


def _aws_features_configured() -> bool:
    """True when any AWS-backed feature is configured for this host.

    We only spend an STS round-trip when AWS actually matters: a webhook
    secret in Secrets Manager, an explicit AWS profile, or a shipped-summary
    bucket. A host with a plain ``SLACK_WEBHOOK_URL`` and no AWS never pays.
    """
    if (config_value("SLACK_WEBHOOK_SECRET_ID") or "").strip():
        return True
    for name in ("AWS_PROFILE", "ALFRED_AWS_PROFILE", "ALFRED_ARTIFACT_BUCKET"):
        if (config_value(name) or "").strip():
            return True
    return False


def _configured_aws_profile() -> str:
    """Resolve the AWS profile Alfred's secret refresh runs under, if any.

    Precedence matches the runtime: an explicit ``AWS_PROFILE`` in the
    process env, then ``ALFRED_AWS_PROFILE`` (env or ``$ALFRED_HOME/.env``).
    Empty string means "no named profile; use ambient credentials".
    """
    return (config_value("AWS_PROFILE") or config_value("ALFRED_AWS_PROFILE") or "").strip()


def check_aws_credentials(
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    features_configured: Callable[[], bool] = _aws_features_configured,
    profile_resolver: Callable[[], str] = _configured_aws_profile,
) -> Finding:
    """Cheap ``sts get-caller-identity`` when AWS-backed features are configured.

    Expired AWS SSO silently degraded Slack secret refresh (the webhook cache
    went stale for 55h before anyone noticed). A short-timeout STS probe
    catches the expiry as soon as fleet-doctor next runs. Skipped entirely
    (green, informational) when no AWS-backed feature is configured.

    When a named AWS profile is configured (``AWS_PROFILE`` /
    ``ALFRED_AWS_PROFILE``), the probe targets that profile with ``--profile``
    so it validates the SAME identity secret refresh uses, not whatever
    default/inherited credentials happen to be in the ambient env. Otherwise a
    valid default profile could mask an expired Alfred profile (false green),
    or vice-versa (false red).
    """
    if not features_configured():
        return Finding("aws-credentials", "green", "no AWS-backed features configured; skipped")

    def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=AWS_STS_TIMEOUT_SECONDS,
            check=False,
        )

    run_it = runner or _default_runner
    profile = profile_resolver()
    cmd = ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"]
    if profile:
        cmd.extend(["--profile", profile])
    label = f" (profile {profile})" if profile else ""
    try:
        result = run_it(cmd)
    except subprocess.TimeoutExpired:
        return Finding(
            "aws-credentials",
            "alert",
            f"🔴 `aws sts get-caller-identity`{label} timed out after "
            f"{AWS_STS_TIMEOUT_SECONDS}s. AWS-backed secret refresh (Slack webhook) may be "
            "degraded. Re-auth AWS SSO.",
        )
    except FileNotFoundError:
        return Finding(
            "aws-credentials",
            "yellow",
            "AWS-backed features configured but `aws` CLI not on PATH.",
        )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        detail = tail[-1][:160] if tail else f"exit {result.returncode}"
        return Finding(
            "aws-credentials",
            "alert",
            f"🔴 AWS credentials not valid{label}: {detail}. AWS-backed secret refresh "
            "(Slack webhook) is degraded until re-auth (e.g. `aws sso login`).",
        )
    return Finding(
        "aws-credentials",
        "green",
        f"AWS credentials valid{label} (sts get-caller-identity ok)",
    )


def check_webhook_cache_age(
    *,
    now: float | None = None,
    stale_hours: int = WEBHOOK_CACHE_STALE_HOURS,
) -> Finding:
    """Warn when the Slack webhook disk cache has gone stale.

    The webhook cache is refreshed from AWS Secrets Manager on a successful
    resolution. When AWS auth lapses the cache stops refreshing and silently
    ages; a stale cache means the fleet is one webhook-rotation away from
    losing its Slack voice. Green when the cache is absent (env-configured
    webhook, no cache needed) or fresh.
    """
    import time as _time

    from agent_runner.paths import SLACK_WEBHOOK_CACHE

    if not SLACK_WEBHOOK_CACHE.exists():
        return Finding("webhook-cache", "green", "no webhook disk cache (env webhook or unused)")
    now_ts = _time.time() if now is None else now
    try:
        age_hours = (now_ts - SLACK_WEBHOOK_CACHE.stat().st_mtime) / 3600.0
    except OSError as exc:
        return Finding("webhook-cache", "yellow", f"could not stat webhook cache: {exc}")
    if age_hours >= stale_hours:
        return Finding(
            "webhook-cache",
            "yellow",
            f"Slack webhook cache is {age_hours:.0f}h old (>{stale_hours}h). AWS secret "
            "refresh may be stalled; verify AWS auth.",
        )
    return Finding("webhook-cache", "green", f"Slack webhook cache fresh ({age_hours:.0f}h old)")


def check_slack_listener(
    *,
    token_reader: Callable[[str], str] = config_value,
    process_probe: Callable[[str], bool] | None = None,
) -> Finding:
    """Warn when the Slack listener is configured but not running.

    ``SLACK_APP_TOKEN`` present means inbound Socket Mode is expected, but if
    no ``alfred-slack-listener`` process exists the fleet is deaf to inbound
    Slack with zero error surface. Green when no app token is configured
    (inbound not expected) or a listener process is found.
    """
    app_token = (token_reader("SLACK_APP_TOKEN") or "").strip()
    if not app_token:
        return Finding(
            "slack-listener",
            "green",
            "SLACK_APP_TOKEN not configured; inbound listener not expected",
        )

    def _default_probe(pattern: str) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    probe = process_probe or _default_probe
    if probe("alfred-slack-listener"):
        return Finding("slack-listener", "green", "alfred-slack-listener process running")
    return Finding(
        "slack-listener",
        "alert",
        (
            "🔴 SLACK_APP_TOKEN is configured but no alfred-slack-listener process is running. "
            "Inbound Slack is silently deaf. Install/start the listener launchd job "
            "(see docs on `alfred-slack-listener`)."
        ),
    )


# ---------------------------------------------------------------------------
# Throttled stale-credential Slack warn (once per day per credential)
#
# State file at $ALFRED_HOME/state/fleet-doctor/last-credential-warn.json maps
# a credential key -> ISO date of the last warn, mirroring the cleanup-warn
# de-dup pattern. A red credential finding pings Slack at most once per UTC day
# per credential so a persistent expiry does not flood the channel.
# ---------------------------------------------------------------------------

_CREDENTIAL_WARN_STATE = STATE_ROOT / "fleet-doctor" / "last-credential-warn.json"
_CREDENTIAL_WARN_KEYS = ("claude-credential", "aws-credentials", "slack-listener")


def _load_credential_warn_state() -> dict[str, str]:
    try:
        data = json.loads(_CREDENTIAL_WARN_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _should_warn_credential(key: str, *, today: str) -> bool:
    """True when ``key`` has not already warned today. Fail-open on a bad file."""
    state = _load_credential_warn_state()
    return state.get(key) != today


def _record_credential_warn(key: str, *, today: str) -> None:
    state = _load_credential_warn_state()
    state[key] = today
    try:
        _CREDENTIAL_WARN_STATE.parent.mkdir(parents=True, exist_ok=True)
        _CREDENTIAL_WARN_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def warn_stale_credentials(
    findings: Sequence[Finding],
    *,
    today: str | None = None,
    poster: Callable[[str], bool] = slack_post,
) -> list[str]:
    """Post a throttled Slack warn for each newly-alerting credential finding.

    Returns the list of credential keys warned this call (empty when nothing
    new). Throttled to once per UTC day per credential via the state file, so
    a persistent expiry pings once, not every firing.
    """
    day = today or datetime.now(UTC).strftime("%Y-%m-%d")
    warned: list[str] = []
    for finding in findings:
        if finding.name not in _CREDENTIAL_WARN_KEYS or finding.severity != "alert":
            continue
        if not _should_warn_credential(finding.name, today=day):
            continue
        if poster(f"[fleet-doctor] credential warning: {finding.message}"):
            _record_credential_warn(finding.name, today=day)
            warned.append(finding.name)
    return warned


CHECKS = [
    check_disk_pressure,
    check_paused_repos,
    check_global_block,
    check_stale_worktrees,
    check_enabled_agents,
    check_paused_agents,
    check_spend_state,
    check_claude_credential,
    check_engine_quota_backoff,
    check_aws_credentials,
    check_webhook_cache_age,
    check_slack_listener,
    check_engine_auth_streak,
]


def run_all_checks() -> list[Finding]:
    """Run every check, swallowing per-check exceptions so a single
    bug doesn't prevent the operator from seeing the rest of the
    snapshot."""
    findings: list[Finding] = []
    for fn in CHECKS:
        try:
            findings.append(fn())
        except Exception as e:
            findings.append(
                Finding(
                    fn.__name__.removeprefix("check_"),
                    "yellow",
                    f"check failed: {type(e).__name__}: {e}",
                )
            )
    return findings


def overall_severity(findings: list[Finding]) -> str:
    """Worst-wins. Empty findings → green."""
    rank = max((SEVERITY_RANK[f.severity] for f in findings), default=0)
    for sev, r in SEVERITY_RANK.items():
        if r == rank:
            return sev
    return "green"


def format_summary(findings: list[Finding]) -> str:
    """Markdown body grouped green / yellow / red. Empty buckets are
    dropped so a healthy day shows just the green section."""
    by_sev: dict[str, list[Finding]] = {"alert": [], "yellow": [], "green": []}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)
    sections: list[str] = []
    if by_sev["alert"]:
        sections.append("*ALERT*\n" + "\n".join(f"• {f.message}" for f in by_sev["alert"]))
    if by_sev["yellow"]:
        sections.append("*YELLOW*\n" + "\n".join(f"• {f.message}" for f in by_sev["yellow"]))
    if by_sev["green"]:
        sections.append("*GREEN*\n" + "\n".join(f"• {f.message}" for f in by_sev["green"]))
    return "\n\n".join(sections)


def main() -> int:
    if doctor_mode():
        print("[FLEET-DOCTOR-OK]")
        return 0

    spec = PreflightSpec(agent=AGENT)
    try:
        preflight(spec)
    except Exception as e:
        print(f"[FLEET-DOCTOR-PREFLIGHT-FAIL] {e}", file=sys.stderr)
        return 0

    with_lock(AGENT)
    findings = run_all_checks()
    sev = overall_severity(findings)
    body = format_summary(findings)

    # Throttled per-credential Slack warn for newly-stale credentials, so a
    # persistent expiry (AWS SSO lapse, missing OAuth token, dead listener)
    # pings the operator once per day even if it is not the worst finding in
    # the snapshot. Best-effort: a Slack failure here must not fail the run.
    try:
        warn_stale_credentials(findings)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[fleet-doctor] credential-warn skipped: {type(e).__name__}: {e}", file=sys.stderr)

    firing_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    summary = f"fleet snapshot · {sev}"
    handle = firing_thread_root(
        codename=AGENT,
        firing_id=firing_id,
        summary_one_liner=summary,
        severity=SEVERITY_TO_SLACK[sev],
        body=body,
    )
    if handle is None:
        slack_post(
            f"[FLEET-DOCTOR] {summary}\n{body}",
            severity=SEVERITY_TO_SLACK[sev],
        )
    print(f"[FLEET-DOCTOR-{sev.upper()}] {len(findings)} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

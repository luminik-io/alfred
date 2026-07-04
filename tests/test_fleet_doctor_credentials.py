"""Tests for the credential / engine-quota / listener fleet-doctor checks.

These are the checks added for the last-24h incident set: silent claude-401
(missing OAuth token), AWS SSO expiry degrading Slack secret refresh, a stale
webhook cache, and a configured-but-dead Slack listener. Each check is a pure
``Finding``-returning function with injectable dependencies, so the tests stub
the subprocess / reader calls rather than touching the host.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "bin" / "fleet-doctor.py"


@pytest.fixture(autouse=True)
def _isolated_alfred_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    for mod in list(sys.modules):
        if mod.startswith("agent_runner") or mod in ("slack_format", "fleet_doctor"):
            del sys.modules[mod]
    sys.path.insert(0, str(REPO / "lib"))
    yield


def _load_doctor():
    spec = importlib.util.spec_from_file_location("fleet_doctor", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fleet_doctor"] = mod
    spec.loader.exec_module(mod)
    return mod


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["cmd"], returncode, stdout, stderr)


# --------------------------------------------------------------------------
# check_claude_credential (item 2a)
# --------------------------------------------------------------------------


def test_claude_credential_green_when_token_present():
    fd = _load_doctor()
    finding = fd.check_claude_credential(
        engine_resolver=lambda _a: "hybrid",
        credential_reader=lambda _k: "sk-oauth-token",
        agents=["lucius", "batman"],
    )
    assert finding.severity == "green"


def test_claude_credential_alert_when_token_missing():
    fd = _load_doctor()
    finding = fd.check_claude_credential(
        engine_resolver=lambda _a: "hybrid",
        credential_reader=lambda _k: "",
        agents=["lucius"],
    )
    assert finding.severity == "alert"
    assert "setup-token" in finding.message
    assert "401" in finding.message


def test_claude_credential_green_when_no_claude_agents():
    fd = _load_doctor()
    # Every agent resolves to codex -> the OAuth token is not required.
    finding = fd.check_claude_credential(
        engine_resolver=lambda _a: "codex",
        credential_reader=lambda _k: "",
        agents=["some-codex-agent"],
    )
    assert finding.severity == "green"


# --------------------------------------------------------------------------
# check_engine_quota_backoff (item 1b surfaced)
# --------------------------------------------------------------------------


def test_engine_quota_green_when_none_parked():
    fd = _load_doctor()
    finding = fd.check_engine_quota_backoff(backoff_reader=lambda _e: None)
    assert finding.severity == "green"


def test_engine_quota_yellow_when_engine_parked():
    fd = _load_doctor()

    def reader(engine):
        if engine == "codex":
            return {"engine": "codex", "until": "2999-01-01T00:00:00Z", "reason": "wall"}
        return None

    finding = fd.check_engine_quota_backoff(backoff_reader=reader)
    assert finding.severity == "yellow"
    assert "codex" in finding.message
    assert "2999" in finding.message


# --------------------------------------------------------------------------
# check_aws_credentials (item 3)
# --------------------------------------------------------------------------


def test_aws_credentials_skipped_when_not_configured():
    fd = _load_doctor()
    finding = fd.check_aws_credentials(features_configured=lambda: False)
    assert finding.severity == "green"
    assert "skipped" in finding.message


def test_aws_credentials_green_when_valid():
    fd = _load_doctor()
    finding = fd.check_aws_credentials(
        features_configured=lambda: True,
        runner=lambda _cmd: _proc(0, stdout="arn:aws:iam::123:user/agent"),
    )
    assert finding.severity == "green"


def test_aws_credentials_alert_when_invalid():
    fd = _load_doctor()
    finding = fd.check_aws_credentials(
        features_configured=lambda: True,
        runner=lambda _cmd: _proc(255, stderr="The SSO session has expired"),
    )
    assert finding.severity == "alert"
    assert "re-auth" in finding.message.lower() or "sso" in finding.message.lower()


def test_aws_credentials_alert_on_timeout():
    fd = _load_doctor()

    def _timeout(_cmd):
        raise subprocess.TimeoutExpired(cmd="aws", timeout=10)

    finding = fd.check_aws_credentials(
        features_configured=lambda: True,
        runner=_timeout,
    )
    assert finding.severity == "alert"
    assert "timed out" in finding.message


def test_aws_credentials_probes_configured_profile():
    """When a named profile is configured, the STS probe targets it with
    --profile so it validates the identity secret refresh actually uses."""
    fd = _load_doctor()
    seen: list[list[str]] = []

    def runner(cmd):
        seen.append(list(cmd))
        return _proc(0, stdout="arn:aws:iam::123:user/alfred")

    finding = fd.check_aws_credentials(
        features_configured=lambda: True,
        profile_resolver=lambda: "alfred-secrets",
        runner=runner,
    )
    assert finding.severity == "green"
    assert seen and "--profile" in seen[0]
    assert seen[0][seen[0].index("--profile") + 1] == "alfred-secrets"
    assert "alfred-secrets" in finding.message


def test_aws_credentials_no_profile_flag_when_unset():
    fd = _load_doctor()
    seen: list[list[str]] = []

    def runner(cmd):
        seen.append(list(cmd))
        return _proc(0, stdout="arn:aws:iam::123:role/default")

    fd.check_aws_credentials(
        features_configured=lambda: True,
        profile_resolver=lambda: "",
        runner=runner,
    )
    assert seen and "--profile" not in seen[0]


# --------------------------------------------------------------------------
# check_webhook_cache_age (item 3)
# --------------------------------------------------------------------------


def test_webhook_cache_green_when_absent():
    fd = _load_doctor()
    finding = fd.check_webhook_cache_age()
    assert finding.severity == "green"


def test_webhook_cache_yellow_when_stale(monkeypatch):
    fd = _load_doctor()
    from agent_runner.paths import SLACK_WEBHOOK_CACHE

    SLACK_WEBHOOK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SLACK_WEBHOOK_CACHE.write_text("https://hooks.slack.com/x")
    # Force the mtime 60h into the past.
    old = time.time() - 60 * 3600
    import os as _os

    _os.utime(SLACK_WEBHOOK_CACHE, (old, old))
    finding = fd.check_webhook_cache_age()
    assert finding.severity == "yellow"
    assert "stalled" in finding.message or "old" in finding.message


def test_webhook_cache_green_when_fresh():
    fd = _load_doctor()
    from agent_runner.paths import SLACK_WEBHOOK_CACHE

    SLACK_WEBHOOK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SLACK_WEBHOOK_CACHE.write_text("https://hooks.slack.com/x")
    finding = fd.check_webhook_cache_age()
    assert finding.severity == "green"


# --------------------------------------------------------------------------
# check_slack_listener (item 4)
# --------------------------------------------------------------------------


def test_slack_listener_green_when_no_app_token():
    fd = _load_doctor()
    finding = fd.check_slack_listener(
        token_reader=lambda _k: "",
        process_probe=lambda _p: False,
    )
    assert finding.severity == "green"


def test_slack_listener_green_when_running():
    fd = _load_doctor()
    finding = fd.check_slack_listener(
        token_reader=lambda _k: "xapp-token",
        process_probe=lambda _p: True,
    )
    assert finding.severity == "green"


def test_slack_listener_alert_when_configured_but_dead():
    fd = _load_doctor()
    finding = fd.check_slack_listener(
        token_reader=lambda _k: "xapp-token",
        process_probe=lambda _p: False,
    )
    assert finding.severity == "alert"
    assert "listener" in finding.message.lower()


# --------------------------------------------------------------------------
# warn_stale_credentials throttle (item 3 de-dup)
# --------------------------------------------------------------------------


def test_warn_stale_credentials_throttles_per_day():
    fd = _load_doctor()
    posted: list[str] = []

    findings = [
        fd.Finding("aws-credentials", "alert", "AWS creds expired"),
        fd.Finding("disk-pressure", "alert", "disk full"),  # not a credential key
    ]

    # First call warns once for the credential finding only.
    warned = fd.warn_stale_credentials(
        findings, today="2026-07-03", poster=lambda m: posted.append(m) or True
    )
    assert warned == ["aws-credentials"]
    assert len(posted) == 1

    # Same day, same finding -> throttled (no new post).
    warned2 = fd.warn_stale_credentials(
        findings, today="2026-07-03", poster=lambda m: posted.append(m) or True
    )
    assert warned2 == []
    assert len(posted) == 1

    # Next day -> warns again.
    warned3 = fd.warn_stale_credentials(
        findings, today="2026-07-04", poster=lambda m: posted.append(m) or True
    )
    assert warned3 == ["aws-credentials"]
    assert len(posted) == 2

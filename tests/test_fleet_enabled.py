"""Tests for the runner-level fleet gate in lib/agent_runner.py.

Covers: file-missing default behaviour, file-present default fallback,
comment handling, atomic writes, idempotence, round-trip through
enable_agent / disable_agent.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_alfred_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    for mod in list(sys.modules):
        if mod == "agent_runner" or mod.startswith("agent_runner."):
            del sys.modules[mod]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    yield


def test_is_agent_enabled_returns_default_when_file_missing():
    import agent_runner as ar

    assert not ar.FLEET_ENABLED_FILE.exists()
    # Default-enabled (opt-out) for stable agents.
    assert ar.is_agent_enabled("senior-dev") is True
    assert ar.is_agent_enabled("senior-dev", default=True) is True
    # Default-disabled (opt-in) for new/in-burn-in agents.
    assert ar.is_agent_enabled("architect", default=False) is False


def test_is_agent_enabled_respects_default_when_file_present():
    import agent_runner as ar

    ar.FLEET_ENABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ar.FLEET_ENABLED_FILE.write_text("architect\nsenior-dev\n")
    # Listed: enabled regardless of default.
    assert ar.is_agent_enabled("architect", default=False) is True
    assert ar.is_agent_enabled("senior-dev", default=False) is True
    # Not listed: the caller's default still decides. This lets the same file
    # gate opt-in runners while normal launchd-scheduled agents remain visible
    # and runnable unless explicitly paused/unloaded.
    assert ar.is_agent_enabled("fixer", default=True) is True
    assert ar.is_agent_enabled("fixer", default=False) is False


def test_read_enabled_codenames_skips_blank_and_comments():
    import agent_runner as ar

    ar.FLEET_ENABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ar.FLEET_ENABLED_FILE.write_text(
        "# This file managed by alfred CLI\n"
        "\n"
        "architect\n"
        "  # indented comment\n"
        "senior-dev # MVP burn-in\n"
        "\n"
    )
    out = ar.list_enabled_agents()
    assert out == ["architect", "senior-dev"]


def test_read_enabled_codenames_preserves_custom_names_that_match_theme_names():
    import agent_runner as ar

    ar.FLEET_ENABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ar.FLEET_ENABLED_FILE.write_text("batman\nnightwing\n")

    assert ar.list_enabled_agents() == ["batman", "nightwing"]
    assert ar.is_agent_enabled("batman", default=False) is True
    assert ar.is_agent_enabled("nightwing", default=False) is True


def test_enable_agent_round_trip():
    import agent_runner as ar

    out = ar.enable_agent("architect")
    assert "architect" in out
    assert ar.FLEET_ENABLED_FILE.exists()
    assert ar.is_agent_enabled("architect") is True
    assert ar.is_agent_enabled("fixer", default=True) is True  # default-enabled


def test_enable_agent_idempotent():
    import agent_runner as ar

    ar.enable_agent("architect")
    out = ar.enable_agent("architect")
    # Single occurrence even when called twice.
    assert out.count("architect") == 1


def test_enable_and_disable_preserve_custom_names_that_match_theme_names():
    import agent_runner as ar

    assert ar.enable_agent("nightwing") == ["nightwing"]
    assert "nightwing" in ar.FLEET_ENABLED_FILE.read_text()
    assert ar.disable_agent("nightwing") == []


def test_disable_agent_idempotent_when_not_present():
    import agent_runner as ar

    # Disabling a never-enabled agent must not raise and must not change
    # state, idempotent contract per the helper docstring.
    ar.enable_agent("senior-dev")
    out = ar.disable_agent("never-listed")
    assert out == ["senior-dev"]


def test_disable_agent_round_trip():
    import agent_runner as ar

    ar.enable_agent("architect")
    ar.enable_agent("senior-dev")
    out = ar.disable_agent("architect")
    assert out == ["senior-dev"]
    assert ar.is_agent_enabled("architect", default=False) is False
    assert ar.is_agent_enabled("senior-dev") is True


def test_enable_agent_rejects_empty_codename():
    import agent_runner as ar

    with pytest.raises(ValueError):
        ar.enable_agent("")
    with pytest.raises(ValueError):
        ar.enable_agent("   ")


def test_atomic_write_leaves_no_tmp_orphan(tmp_path):
    import agent_runner as ar

    ar.enable_agent("architect")
    # The atomic write must not leave a *.tmp file behind.
    parent = ar.FLEET_ENABLED_FILE.parent
    leftover = list(parent.glob("*.tmp"))
    assert leftover == [], f"unexpected tmp orphans: {leftover}"


def test_write_dedupes_silently():
    import agent_runner as ar

    ar.enable_agent("architect")
    # Manually inject duplicates into the file to mimic a hand-edit, then
    # any subsequent enable/disable should normalize the state.
    ar.FLEET_ENABLED_FILE.write_text("architect\narchitect\nsenior-dev\n")
    out = ar.enable_agent("fixer")
    # Sorted, deduped.
    assert out == ["architect", "fixer", "senior-dev"]


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

CLI = Path(__file__).resolve().parent.parent / "bin" / "alfred"
STATUS_CLI = Path(__file__).resolve().parent.parent / "bin" / "alfred-status.py"


def _load_cli_module():
    loader = importlib.machinery.SourceFileLoader("alfred_cli", str(CLI))
    spec = importlib.util.spec_from_loader("alfred_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_cli"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_status_module():
    # Register in sys.modules before exec so the module's @dataclass decorators
    # can resolve their own module (dataclasses looks it up by __module__).
    loader = importlib.machinery.SourceFileLoader("alfred_status_cli", str(STATUS_CLI))
    spec = importlib.util.spec_from_loader("alfred_status_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_status_cli"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run_cli(*argv: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    for key in list(full_env):
        if key.startswith("ALFRED_") or key in {
            "ALFRED_HOME",
            "GH_ORG",
            "OPERATOR_NAME",
            "WORKSPACE_ROOT",
        }:
            full_env.pop(key, None)
    full_env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(CLI), *argv],
        capture_output=True,
        text=True,
        env=full_env,
    )


def test_cli_enable_then_enabled_agents_round_trip(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    res = _run_cli("enable", "architect", env_extra=env)
    assert res.returncode == 0, res.stderr
    assert "enabled architect" in res.stdout

    res = _run_cli("enabled-agents", env_extra=env)
    assert res.returncode == 0
    assert "architect" in res.stdout


def test_cli_disable_idempotent(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    # Disabling something that was never enabled is fine.
    res = _run_cli("disable", "never-existed", env_extra=env)
    assert res.returncode == 0


def test_cli_enabled_agents_announces_missing_file(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    res = _run_cli("enabled-agents", env_extra=env)
    assert res.returncode == 0
    assert "missing" in res.stdout.lower()


def test_cli_native_dry_run_loads_launcher_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    runtime = tmp_path / "alfred"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (runtime / "launchd").mkdir(parents=True)
    (runtime / ".env").write_text(
        f"WORKSPACE_ROOT={workspace}\nCUSTOM_FROM_ENV=loaded-from-env\n",
        encoding="utf-8",
    )
    (runtime / "launchd" / "agents.conf").write_text(
        "custom.fleet.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("ALFREDRC", str(home / ".alfredrc"))

    cli = _load_cli_module()
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs["env"]

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)

    rc = cli.cmd_dry_run(
        argparse.Namespace(codename="senior-dev", native=True, simulate=False, json=False)
    )

    assert rc == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert captured["cmd"] == [sys.executable, str(CLI.parent / "senior-dev.py"), "--dry-run"]
    assert env["ALFRED_HOME"] == str(runtime)
    assert env["WORKSPACE_ROOT"] == str(workspace)
    assert env["CUSTOM_FROM_ENV"] == "loaded-from-env"
    assert "ALFREDRC" not in env
    assert env["ALFRED_DRY_RUN"] == "1"
    assert env["AGENT_CODENAME"] == "senior-dev"
    assert env["LAUNCHD_LABEL"] == "custom.fleet.senior-dev"
    assert str(CLI.parent.parent / "lib") in env["PYTHONPATH"].split(os.pathsep)
    assert "WORKSPACE_ROOT" not in os.environ


def test_cli_native_dry_run_all_reuses_launcher_env(monkeypatch, tmp_path):
    runtime = tmp_path / "alfred"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (runtime / "launchd").mkdir(parents=True)
    (runtime / "launchd" / "agents.conf").write_text(
        "custom.fleet.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)

    cli = _load_cli_module()
    launcher_calls = 0
    captured: list[dict[str, str]] = []

    def fake_launcher_env():
        nonlocal launcher_calls
        launcher_calls += 1
        if launcher_calls > 1:
            raise AssertionError("dry-run all must reuse the parent launcher env")
        return {
            "ALFRED_HOME": str(runtime),
            "WORKSPACE_ROOT": str(workspace),
            "CUSTOM_FROM_LAUNCHER": "loaded-once",
        }

    class FakeProcess:
        def __init__(self, _cmd, **kwargs):
            captured.append(kwargs["env"])

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli.agent_runner, "launcher_env", fake_launcher_env)
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)

    rc = cli.cmd_dry_run(
        argparse.Namespace(codename="all", native=True, simulate=False, json=False)
    )

    assert rc == 0
    assert launcher_calls == 1
    assert len(captured) == 1
    assert captured[0]["ALFRED_HOME"] == str(runtime)
    assert captured[0]["WORKSPACE_ROOT"] == str(workspace)
    assert captured[0]["CUSTOM_FROM_LAUNCHER"] == "loaded-once"
    assert captured[0]["LAUNCHD_LABEL"] == "custom.fleet.senior-dev"
    assert "ALFRED_HOME" not in os.environ
    assert "WORKSPACE_ROOT" not in os.environ


def test_cli_engine_set_supports_batman(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    res = _run_cli("engine", "set", "architect", "codex", env_extra=env)
    assert res.returncode == 0, res.stderr
    assert "architect engine set to codex" in res.stdout
    assert (tmp_path / "alfred" / "state" / "engines" / "architect").read_text().strip() == "codex"

    status = _run_cli("engine", "status", "architect", env_extra=env)
    assert status.returncode == 0, status.stderr
    assert "architect engine: codex" in status.stdout


def test_cli_engine_status_lists_known_agents(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    res = _run_cli("engine", "status", env_extra=env)
    assert res.returncode == 0, res.stderr
    for agent in (
        "test-engineer",
        "architect",
        "planner",
        "senior-dev",
        "fixer",
        "reviewer",
        "triage",
    ):
        assert agent in res.stdout
    assert "Codex fallback only on capability gaps" in res.stdout
    assert "auth/limit/budget" not in res.stdout


def test_cli_engine_status_uses_custom_agent_manifest_default(tmp_path):
    alfred = tmp_path / "alfred"
    custom_dir = alfred / "state" / "custom-agents"
    custom_dir.mkdir(parents=True)
    (custom_dir / "custom-agents.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agents": [
                    {
                        "codename": "release-captain",
                        "display_name": "Release Captain",
                        "role_title": "Release coordinator",
                        "purpose": "Checks release readiness.",
                        "prompt": "Review release readiness and summarize blockers.",
                        "engine": "codex",
                        "schedule": "interval:1800",
                        "repos": [],
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "ALFRED_ENGINE": "",
    }

    res = _run_cli("engine", "status", "release-captain", env_extra=env)

    assert res.returncode == 0, res.stderr
    assert "release-captain engine: codex" in res.stdout
    assert f"state file: {alfred / 'state' / 'engines' / 'release-captain'}" in res.stdout

    engine_state = alfred / "state" / "engines"
    engine_state.mkdir(parents=True)
    (engine_state / "release-captain").write_text("claude\n", encoding="utf-8")
    overridden = _run_cli("engine", "status", "release-captain", env_extra=env)
    assert overridden.returncode == 0, overridden.stderr
    assert "release-captain engine: claude" in overridden.stdout


def test_cli_codex_status_reports_binary_and_engines(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    codex = fake_bin / "codex"
    codex.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo codex-test; exit 0; fi\n')
    codex.chmod(0o755)
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }

    res = _run_cli("codex", "status", env_extra=env)

    assert res.returncode == 0, res.stderr
    assert "codex version: codex-test" in res.stdout
    assert "engine senior-dev:" in res.stdout
    assert "Probe with: alfred codex probe" in res.stdout


def test_cli_codex_status_fails_when_binary_missing(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "PATH": str(tmp_path / "empty-bin"),
    }

    res = _run_cli("codex", "status", env_extra=env)

    assert res.returncode == 1
    assert "codex: not found" in res.stderr


def test_claude_routing_reads_systemd_environment(monkeypatch, tmp_path):
    cli = _load_cli_module()
    monkeypatch.setattr(cli.scheduler, "SCHEDULER", "systemd")
    target = tmp_path / "claude-secondary"

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["systemctl", "--user", "show-environment"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=f"PATH=/usr/bin\nCLAUDE_CONFIG_DIR={target}\n",
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._current_claude_dir() == str(target)


def test_claude_routing_decodes_systemd_escaped_environment(monkeypatch, tmp_path):
    cli = _load_cli_module()
    monkeypatch.setattr(cli.scheduler, "SCHEDULER", "systemd")
    target = tmp_path / "home with spaces" / ".claude-secondary"

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["systemctl", "--user", "show-environment"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=f"PATH=/usr/bin\nCLAUDE_CONFIG_DIR=$'{target}'\n",
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._current_claude_dir() == str(target)


def test_claude_primary_sets_systemd_environment(monkeypatch, tmp_path):
    cli = _load_cli_module()
    monkeypatch.setattr(cli.scheduler, "SCHEDULER", "systemd")
    home = tmp_path / "home"
    primary = home / ".claude"
    primary.mkdir(parents=True)
    cli.PRIMARY_CLAUDE_DIR = primary
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._set_claude_dir(primary, "primary") == 0
    assert ["systemctl", "--user", "set-environment", f"CLAUDE_CONFIG_DIR={primary}"] in calls


def test_cli_auth_status_propagates_codex_status_failure(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl = fake_bin / "launchctl"
    launchctl.write_text("#!/bin/sh\nexit 0\n")
    launchctl.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "PATH": str(fake_bin),
    }

    res = _run_cli("auth", "status", env_extra=env)

    assert res.returncode == 1
    assert "Current routing for scheduled agents" in res.stdout
    assert "codex: not found" in res.stderr


def test_cli_status_reports_local_snapshot(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "my.fleet.architect\tarchitect.py\tinterval:5400\tno\t\tBundle coordinator\n"
    )
    wait_dir = alfred / "state" / "architect" / "approval-waits"
    wait_dir.mkdir(parents=True)
    (wait_dir / "firing.json").write_text(
        '{"firing_id":"abc","pid":0,"created_at":"2026-05-12T10:00:00Z","issues":[{"number":504}]}'
    )
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    events_dir = alfred / "state" / "architect" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / f"{day.replace('-', '')}-101500-abcd.jsonl").write_text(
        f'{{"ts":"{day}T10:15:00Z","agent":"architect","firing_id":"abc","event":"firing_started"}}\n'
        f'{{"ts":"{day}T10:15:05Z","agent":"architect","firing_id":"abc","event":"firing_complete","outcome":"silent_no_work"}}\n'
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("status", env_extra=env)

    assert res.returncode == 0, res.stderr
    assert "alfred-status @" in res.stdout
    assert "approval wait dead #504" in res.stdout
    # The human table shows the roster theme's name; the default Batman theme
    # renders the ``architect`` slug as "Batman".
    batman_row = next(line for line in res.stdout.splitlines() if line.startswith("Batman"))
    assert " 1     0   0" in batman_row


def test_cli_status_human_name_follows_persisted_roster_theme(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "my.fleet.architect\tarchitect.py\tinterval:5400\tno\t\tBundle coordinator\n"
    )
    theme_dir = alfred / "state" / "roster-theme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "roster-theme.json").write_text(
        json.dumps({"version": 1, "theme": "transformers", "custom_names": {}, "custom_roles": {}}),
        encoding="utf-8",
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("status", env_extra=env)

    assert res.returncode == 0, res.stderr
    # The persisted Transformers theme renames the ``architect`` slug's human
    # column to "Optimus Prime", matching the desktop; the raw slug never shows.
    assert any(line.startswith("Optimus Prime") for line in res.stdout.splitlines())
    assert not any(line.startswith("architect") for line in res.stdout.splitlines())


def test_cli_status_json_keeps_raw_slug_and_adds_themed_name(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "my.fleet.architect\tarchitect.py\tinterval:5400\tno\t\tBundle coordinator\n"
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("status", "--json", env_extra=env)

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    architect = next(a for a in payload["agents"] if a["agent"] == "architect")
    # Machine-readable JSON keeps the raw slug on ``agent`` and exposes the themed
    # human name separately so downstream consumers are not broken by the rename.
    assert architect["agent"] == "architect"
    assert architect["display_name"] == "Batman"


def test_status_falls_back_to_checkout_agents_conf_when_runtime_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    runtime = tmp_path / "fresh-runtime"
    checkout = tmp_path / "checkout"
    (checkout / "bin").mkdir(parents=True)
    (checkout / "launchd").mkdir()
    checkout_conf = checkout / "launchd" / "agents.conf"
    checkout_conf.write_text(
        "my.fleet.checkout-only\tarchitect.py\tinterval:5400\tno\t\tCheckout marker\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFRED_HOME", str(runtime))

    status = _load_status_module()
    monkeypatch.setattr(status, "_HERE", checkout / "bin")

    records = status.configured_agents()

    assert [record.label for record in records] == ["my.fleet.checkout-only"]
    assert records[0].role == "Checkout marker"


def _status_snapshot(status_mod, **overrides):
    """Build an AgentSnapshot with sane defaults; override only what a test needs."""
    fields = {
        "agent": "release-captain",
        "display_name": "Release Captain",
        "label": "alfred.release-captain",
        "role": "Release coordinator",
        "schedule": "-",
        "loaded": True,
        "disabled": False,
        "engine": "codex",
        "locked": False,
        "stale_lock": False,
        "lock_pid": None,
        "lock_age_seconds": None,
        "paused": False,
        "paused_since": None,
        "last_fired": None,
        "last_event": None,
        "today_firings": 0,
        "today_successes": 0,
        "today_failures": 0,
        "today_consecutive_failures": 0,
        "today_turns": 0,
        "today_cost_usd": 0.0,
        "blocked_until": None,
        "last_stderr_tail": None,
        "approval_wait_firing_id": None,
        "approval_wait_issue_numbers": [],
        "approval_wait_created_at": None,
        "approval_wait_age_seconds": None,
        "approval_wait_pid": None,
        "approval_wait_pid_alive": None,
    }
    fields.update(overrides)
    return status_mod.AgentSnapshot(**fields)


def test_render_slack_escapes_mrkdwn_in_display_name():
    status = _load_status_module()
    ZWSP = "​"
    # A custom agent's display name (or a custom roster-theme label) can carry
    # mrkdwn markup. render_slack interpolates it into a Slack mrkdwn body, so it
    # must be escaped at both the flagged and approval-wait interpolation sites.
    flagged = _status_snapshot(
        status,
        display_name="*Boss* <@U123> ~x~",
        stale_lock=True,
    )
    waiting = _status_snapshot(
        status,
        display_name="_Deputy_ & `chief`",
        approval_wait_issue_numbers=[7],
        approval_wait_pid_alive=True,
        approval_wait_age_seconds=42.0,
    )
    out = status.render_slack([flagged, waiting], {"global_block": None})

    # Flagged line: markup neutralized, mention escaped to an entity.
    assert "*Boss*" not in out
    assert f"*{ZWSP}Boss*{ZWSP}" in out
    assert "&lt;@U123&gt;" in out
    assert f"~{ZWSP}x~{ZWSP}" in out
    # Approval-wait line: same neutralization on the second interpolation site.
    assert "_Deputy_" not in out
    assert f"_{ZWSP}Deputy_{ZWSP}" in out
    assert "&amp;" in out
    assert f"`{ZWSP}chief`{ZWSP}" in out


def test_render_table_keeps_display_name_raw():
    status = _load_status_module()
    # The plain-text CLI table must NOT escape: the raw operator name shows
    # verbatim, no HTML entities and no zero-width spaces.
    snap = _status_snapshot(status, display_name="*Boss* <@U123>", stale_lock=True)
    table = status.render_table(
        [snap],
        {
            "host_scheduler": "launchd",
            "global_block": None,
            "slack_webhook_cache_age_hours": None,
        },
    )
    assert "*Boss* <@U123>" in table
    assert "​" not in table
    assert "&lt;" not in table


def test_status_ignores_only_expected_disabled_skip_tails():
    status = _load_status_module()
    skips = "\n".join(
        [
            "[ARCHITECT-SKIP] architect not enabled in fleet file; run `alfred enable architect`.",
            "[SPEC-PLANNER-SKIP] spec-planner not enabled in fleet file; run `alfred enable spec-planner`.",
        ]
    )

    assert status._only_expected_disabled_skips(skips) is True
    assert status._only_expected_disabled_skips(f"real failure\n{skips}") is False


def test_status_uses_runtime_gate_for_renamed_opt_in_agents(monkeypatch):
    status = _load_status_module()
    record = status.AgentRecord(
        label="alfred.story-planner",
        codename="story-planner",
        script="spec-planner.py",
        schedule="hourly",
        log_stem="alfred.spec-planner",
        role="Spec planner",
        disabled=False,
    )
    monkeypatch.setattr(status.agent_runner, "is_agent_enabled", lambda *_a, **_kw: False)

    assert status._record_disabled(record) is True


def test_status_checks_themed_and_implementation_noop_markers():
    status = _load_status_module()
    record = status.AgentRecord(
        label="alfred.story-planner",
        codename="story-planner",
        script="spec-planner.py",
        schedule="hourly",
        log_stem="custom.spec-planner",
        role="Spec planner",
        disabled=False,
    )

    assert status._noop_markers(record) == {
        status.STATE_ROOT / "story-planner" / "last-noop",
        status.STATE_ROOT / "spec-planner" / "last-noop",
        Path("/tmp/alfred.story-planner.noop"),
        Path("/tmp/alfred.spec-planner.noop"),
    }


def test_cli_status_uses_custom_agent_manifest_engine_default(tmp_path):
    alfred = tmp_path / "alfred"
    custom_dir = alfred / "state" / "custom-agents"
    custom_dir.mkdir(parents=True)
    (custom_dir / "custom-agents.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agents": [
                    {
                        "codename": "release-captain",
                        "display_name": "Release Captain",
                        "role_title": "Release coordinator",
                        "purpose": "Checks release readiness.",
                        "prompt": "Review release readiness and summarize blockers.",
                        "engine": "codex",
                        "schedule": "interval:1800",
                        "repos": [],
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "ALFRED_ENGINE": "",
    }

    res = _run_cli("status", env_extra=env)

    assert res.returncode == 0, res.stderr
    # A custom agent the roster theme does not know keeps its manifest
    # display name in the human table.
    row = next(line for line in res.stdout.splitlines() if line.startswith("Release Captain"))
    assert row.split()[3] == "codex"
    # A fleet slug renders under the default Batman theme ("architect" -> "Batman").
    assert any(line.startswith("Batman") for line in res.stdout.splitlines())

    engine_state = alfred / "state" / "engines"
    engine_state.mkdir(parents=True)
    (engine_state / "release-captain").write_text("claude\n", encoding="utf-8")
    overridden = _run_cli("status", env_extra=env)
    row = next(
        line for line in overridden.stdout.splitlines() if line.startswith("Release Captain")
    )
    assert row.split()[3] == "claude"


def test_cli_status_treats_empty_runtime_conf_as_authoritative(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    custom_dir = alfred / "state" / "custom-agents"
    launchd.mkdir(parents=True)
    custom_dir.mkdir(parents=True)
    (launchd / "agents.conf").write_text("", encoding="utf-8")
    (custom_dir / "custom-agents.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agents": [
                    {
                        "codename": "release-captain",
                        "display_name": "Release Captain",
                        "role_title": "Release coordinator",
                        "purpose": "Checks release readiness.",
                        "prompt": "Review release readiness and summarize blockers.",
                        "engine": "codex",
                        "schedule": "interval:1800",
                        "repos": [],
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = _run_cli(
        "status",
        env_extra={
            "ALFRED_HOME": str(alfred),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        },
    )

    assert res.returncode == 0, res.stderr
    rows = res.stdout.splitlines()
    # An empty runtime conf drops the fleet; only the custom agent remains, shown
    # by its manifest display name. No fleet slug (Batman) leaks in.
    assert any(line.startswith("Release Captain") for line in rows)
    assert not any(line.startswith("Batman") for line in rows)


def test_cli_engine_set_accepts_configured_runtime_codename(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "my.fleet.marshall\tsenior-dev.py\tinterval:1200\tyes\t\tCustom feature engineer\n"
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("engine", "set", "marshall", "codex", env_extra=env)
    assert res.returncode == 0, res.stderr
    assert "marshall engine set to codex" in res.stdout
    assert (alfred / "state" / "engines" / "marshall").read_text().strip() == "codex"

    status = _run_cli("engine", "status", "marshall", env_extra=env)
    assert status.returncode == 0, status.stderr
    assert "marshall engine: codex" in status.stdout


def test_cli_engine_set_rasalghul_uses_canonical_engine_state_only(tmp_path):
    alfred = tmp_path / "alfred"
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("engine", "set", "reviewer", "codex", env_extra=env)

    assert res.returncode == 0, res.stderr
    assert (alfred / "state" / "engines" / "reviewer").read_text().strip() == "codex"
    assert not (alfred / "state" / "review-engine").exists()


def test_cli_review_engine_alias_is_not_exposed(tmp_path):
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    help_res = _run_cli("--help", env_extra=env)
    assert help_res.returncode == 0, help_res.stderr
    assert "review-engine" not in help_res.stdout

    res = _run_cli("review-engine", "status", env_extra=env)
    assert res.returncode == 2
    assert "invalid choice" in res.stderr


def test_cli_agents_does_not_disable_default_agents_when_gate_file_exists(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "my.fleet.architect\tarchitect.py\tinterval:5400\tno\t\tBundle coordinator\n"
        "my.fleet.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tFeature dev\n"
    )
    gate = alfred / "state" / "fleet"
    gate.mkdir(parents=True)
    (gate / "enabled.txt").write_text("architect\n")
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("agents", env_extra=env)
    assert res.returncode == 0, res.stderr
    lines = {
        line.split()[0]: line
        for line in res.stdout.splitlines()
        if line.startswith(("architect", "senior-dev"))
    }
    assert "yes" in lines["architect"].split()
    assert "yes" in lines["senior-dev"].split()


def test_cli_agents_keeps_renamed_spec_planner_opt_in(tmp_path):
    alfred = tmp_path / "alfred"
    launchd = alfred / "launchd"
    launchd.mkdir(parents=True)
    (launchd / "agents.conf").write_text(
        "alfred.story-planner\tspec-planner.py\tinterval:3600\tno\t\tSpec planner\n"
    )
    env = {
        "ALFRED_HOME": str(alfred),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    res = _run_cli("agents", env_extra=env)

    assert res.returncode == 0, res.stderr
    row = next(line for line in res.stdout.splitlines() if line.startswith("story-planner"))
    assert row.split()[4] == "no"

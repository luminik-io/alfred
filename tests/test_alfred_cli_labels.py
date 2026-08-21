"""Tests for ``alfred labels`` operator commands."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "alfred"
LIB = REPO_ROOT / "lib"
sys.path.insert(0, str(LIB))


@pytest.fixture()
def cli_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("GH_ORG", "acme")
    for key in list(os.environ):
        if key.startswith("ALFRED_") and key.endswith("_REPOS"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LABEL_STATE_SWEEP_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_CLAIM_SWEEP_REPOS", raising=False)
    loader = SourceFileLoader("alfred_cli_labels", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_cli_labels"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_label_catalogue_includes_lifecycle_architect_and_operator_labels(cli_module) -> None:
    labels = cli_module._label_bootstrap_catalog()
    names = {name for name, _, _ in labels}
    assert "agent:implement" in names
    assert "agent:large-feature" in names
    assert "agent:authored" in names
    assert "agent:plan-pending-approval" in names
    assert "do-not-merge" in names
    descriptions = {name: description for name, _, description in labels}
    assert descriptions["agent:plan-pending-approval"].startswith("The architect role")
    assert "Batman" not in descriptions["agent:plan-pending-approval"]


def test_resolve_label_accepts_only_runtime_ids_or_full_labels(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Roster themes are display-only and cannot steer runtime commands.
    monkeypatch.setattr(
        cli_module,
        "_label_map",
        lambda: {"senior-dev": "alfred.senior-dev", "planner": "alfred.planner"},
    )
    assert cli_module._resolve_label("lucius") is None
    assert cli_module._resolve_label("drake") is None
    assert cli_module._resolve_label("senior-dev") == "alfred.senior-dev"
    assert cli_module._resolve_label("alfred.senior-dev") == "alfred.senior-dev"
    assert cli_module._resolve_label("nonesuch") is None
    assert cli_module._resolve_label("batman") is None

    assert cli_module._resolve_label("ironhide") is None


def test_agents_conf_path_falls_back_to_checkout_when_runtime_missing(
    cli_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "empty-runtime"
    checkout = tmp_path / "checkout"
    runtime.mkdir()
    (checkout / "bin").mkdir(parents=True)
    (checkout / "launchd").mkdir()
    checkout_conf = checkout / "launchd" / "agents.conf"
    checkout_conf.write_text(
        "my.fleet.architect\tarchitect.py\tinterval:3600\tno\t\tArchitect\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setattr(cli_module, "_HERE", checkout / "bin")

    assert cli_module._agents_conf_path() == checkout_conf
    assert [row["label"] for row in cli_module._parse_agents_conf(checkout_conf)] == [
        "my.fleet.architect"
    ]


def test_labels_check_reports_missing_without_creating(
    cli_module, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["gh", "label", "list"]:
            existing = [{"name": "agent:implement", "color": "0e8a16", "description": ""}]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(existing), stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    rc = cli_module.main(["labels", "bootstrap", "your-backend", "--check"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "labels check on acme/your-backend" in out
    assert "agent:in-flight (MISSING)" in out
    assert all(cmd[:3] == ["gh", "label", "list"] for cmd in calls)


def test_labels_bootstrap_creates_missing_labels(
    cli_module, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if cmd[:3] == ["gh", "label", "create"]:
            created.append(cmd[3])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    rc = cli_module.main(["labels", "bootstrap", "your-backend"])

    assert rc == 0
    assert "agent:implement" in created
    assert "agent:large-feature" in created
    assert "do-not-merge" in created
    assert "labels bootstrap on acme/your-backend" in capsys.readouterr().out


def test_labels_all_reads_fleet_repo_env(cli_module, monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path(os.environ["ALFRED_HOME"])
    home.mkdir(parents=True)
    (home / ".env").write_text(
        "GH_ORG=acme\nALFRED_SENIOR_DEV_REPOS=api,web\nALFRED_REVIEWER_REPOS=web,mobile\n",
        encoding="utf-8",
    )
    repos: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_labels_bootstrap_one",
        lambda repo, *, check, force: repos.append(repo) or 0,
    )

    assert cli_module.main(["labels", "check", "--all"]) == 0
    assert repos == ["api", "web", "mobile"]


def test_setup_token_forwards_paste_back_token(cli_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakeProcess)

    assert cli_module.main(["setup-token", "--token", "runtime-token-value"]) == 0
    assert calls == [
        [
            sys.executable,
            str(BIN.parent / "alfred-setup-token.py"),
            "--token",
            "runtime-token-value",
        ]
    ]


def test_labels_all_hydrates_fleet_repo_env_from_runtime_env(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = Path(os.environ["ALFRED_HOME"])
    home.mkdir(parents=True)
    (home / ".env").write_text(
        "GH_ORG=acme\nALFRED_SENIOR_DEV_REPOS=api,web\nALFRED_REVIEWER_REPOS=web,mobile\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALFRED_SENIOR_DEV_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_REVIEWER_REPOS", raising=False)
    repos: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_labels_bootstrap_one",
        lambda repo, *, check, force: repos.append(repo) or 0,
    )

    assert cli_module.main(["labels", "check", "--all"]) == 0
    assert repos == ["api", "web", "mobile"]


def test_runtime_env_loader_expands_home_tokens_like_agent_launch(
    cli_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Path(os.environ["ALFRED_HOME"])
    home.mkdir(parents=True)
    env_file = home / ".env"
    env_file.write_text(
        "WORKSPACE_ROOT=$HOME/work\n"
        "CODEX_HOME=${HOME}/codex\n"
        "ALFRED_LITERAL='$HOME/not-expanded'\n",
        encoding="utf-8",
    )

    values = cli_module._read_env_values(env_file)

    assert values["WORKSPACE_ROOT"] == str(tmp_path / "work")
    assert values["CODEX_HOME"] == str(tmp_path / "codex")
    assert values["ALFRED_LITERAL"] == "$HOME/not-expanded"


def test_runtime_env_file_does_not_clobber_process_overrides(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = Path(os.environ["ALFRED_HOME"])
    home.mkdir(parents=True)
    (home / ".env").write_text(
        "GH_ORG=acme\nALFRED_SENIOR_DEV_REPOS=api,web\nALFRED_TELEMETRY_ENABLED=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFRED_SENIOR_DEV_REPOS", "manual/repo")
    monkeypatch.setenv("ALFRED_TELEMETRY_ENABLED", "0")
    repos: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_labels_bootstrap_one",
        lambda repo, *, check, force: repos.append(repo) or 0,
    )

    assert cli_module.main(["labels", "check", "--all"]) == 0
    assert repos == ["manual/repo"]
    assert os.environ["ALFRED_TELEMETRY_ENABLED"] == "0"


def test_capabilities_command_does_not_import_agent_runner(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("HOME", None)
    env["ALFRED_HOME"] = str(tmp_path / ".alfred")
    env["CODEX_HOME"] = str(tmp_path / "codex")
    env["CLAUDE_HOME"] = str(tmp_path / "claude")
    env["PYTHONPATH"] = str(LIB)
    code = f"""
import builtins
import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "agent_runner" or name.startswith("agent_runner.") or name == "scheduler":
        raise RuntimeError("blocked import should not be needed")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
pathlib.Path.home = staticmethod(
    lambda: (_ for _ in ()).throw(RuntimeError("no home"))
)
loader = SourceFileLoader("alfred_cli_no_agent_runner", {str(BIN)!r})
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = module
spec.loader.exec_module(module)
raise SystemExit(module.main(["capabilities", "--json"]))
"""

    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["summary"]["total"] == 3


def test_clear_lock_clears_dead_lock(
    cli_module, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "agent-lock-lucius"
    lock_dir.mkdir()
    posts: list[str] = []
    monkeypatch.setattr(cli_module, "_lock_dir_for_agent", lambda agent: lock_dir)
    monkeypatch.setattr(cli_module, "_describe_lock", lambda lock, agent: (12345, False, None))
    monkeypatch.setattr(cli_module, "_matching_worktree_risks", lambda agent, **kw: [])
    monkeypatch.setattr(
        cli_module, "_clear_lock_scheduler_health", lambda agent: "scheduler: loaded"
    )
    monkeypatch.setattr(
        cli_module.agent_runner, "slack_post", lambda text: posts.append(text) or True
    )

    assert cli_module.main(["clear-lock", "lucius"]) == 0
    assert not lock_dir.exists()
    out = capsys.readouterr().out
    assert "cleared" in out
    assert "scheduler: loaded" in out
    assert posts == [f"alfred clear-lock: cleared lucius lock at {lock_dir}; scheduler: loaded"]


def test_clear_lock_quiet_skips_slack_post(
    cli_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "agent-lock-lucius"
    lock_dir.mkdir()
    monkeypatch.setattr(cli_module, "_lock_dir_for_agent", lambda agent: lock_dir)
    monkeypatch.setattr(cli_module, "_describe_lock", lambda lock, agent: (12345, False, None))
    monkeypatch.setattr(cli_module, "_matching_worktree_risks", lambda agent, **kw: [])
    monkeypatch.setattr(
        cli_module, "_clear_lock_scheduler_health", lambda agent: "scheduler: loaded"
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "slack_post",
        lambda text: (_ for _ in ()).throw(AssertionError("unexpected Slack post")),
    )

    assert cli_module.main(["clear-lock", "lucius", "--quiet"]) == 0
    assert not lock_dir.exists()


def test_clear_lock_refuses_live_matching_holder(
    cli_module, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "agent-lock-lucius"
    lock_dir.mkdir()
    monkeypatch.setattr(cli_module, "_lock_dir_for_agent", lambda agent: lock_dir)
    monkeypatch.setattr(cli_module, "_describe_lock", lambda lock, agent: (12345, True, True))
    monkeypatch.setattr(cli_module, "_matching_worktree_risks", lambda agent, **kw: [])

    assert cli_module.main(["clear-lock", "lucius"]) == 1
    assert lock_dir.exists()
    assert "refusing to clear" in capsys.readouterr().out


def test_clear_lock_refuses_unknown_holder(
    cli_module, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "agent-lock-lucius"
    lock_dir.mkdir()
    monkeypatch.setattr(cli_module, "_lock_dir_for_agent", lambda agent: lock_dir)
    monkeypatch.setattr(cli_module, "_describe_lock", lambda lock, agent: (None, False, None))
    monkeypatch.setattr(cli_module, "_matching_worktree_risks", lambda agent, **kw: [])

    assert cli_module.main(["clear-lock", "lucius"]) == 1
    assert lock_dir.exists()
    out = capsys.readouterr().out
    assert "pid is unknown" in out
    assert "refusing to clear" in out


def test_clear_lock_refuses_matching_unpushed_worktree(
    cli_module, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "agent-lock-lucius"
    lock_dir.mkdir()
    monkeypatch.setattr(cli_module, "_lock_dir_for_agent", lambda agent: lock_dir)
    monkeypatch.setattr(cli_module, "_describe_lock", lambda lock, agent: (12345, False, None))
    monkeypatch.setattr(
        cli_module,
        "_matching_worktree_risks",
        lambda agent, **kw: ["/tmp/wt-lucius (lucius/42, ahead of remote)"],
    )

    assert cli_module.main(["clear-lock", "lucius"]) == 1
    assert lock_dir.exists()
    out = capsys.readouterr().out
    assert "worktree risk" in out
    assert "refusing to clear" in out


def test_brain_command_forwards_to_brain_cli(cli_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakeProcess)

    assert cli_module.main(["brain", "lessons", "lucius", "org/api"]) == 0
    assert calls == [
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "alfred-brain.py"),
            "lessons",
            "lucius",
            "org/api",
        ]
    ]


def test_code_memory_command_forwards_to_launcher(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakeProcess)

    assert cli_module.main(["code-memory", "doctor"]) == 0
    assert calls == [[str(REPO_ROOT / "bin" / "code-memory-mcp"), "doctor"]]


def test_code_memory_command_defaults_to_doctor(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakeProcess)

    assert cli_module.main(["code-memory"]) == 0
    assert calls == [[str(REPO_ROOT / "bin" / "code-memory-mcp"), "doctor"]]


def test_memory_doctor_json_reports_unified_memory_plane(
    cli_module, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_memory_provider_chain_status",
        lambda env: {
            "status": "ok",
            "names": ["redis", "fleet"],
            "enabled_names": ["redis", "fleet"],
            "detail": "provider chain: redis, fleet",
        },
    )
    monkeypatch.setattr(
        cli_module,
        "_memory_redis_status",
        lambda chain, env: {"status": "ok", "detail": "Redis AMS reachable"},
    )
    monkeypatch.setattr(
        cli_module,
        "_memory_fleet_brain_status",
        lambda chain, env: {"status": "ok", "detail": "FleetBrain database: test.db"},
    )
    monkeypatch.setattr(
        cli_module,
        "_memory_code_memory_status",
        lambda env: {"status": "warn", "detail": "index missing"},
    )
    monkeypatch.setattr(
        cli_module,
        "_memory_code_map_status",
        lambda: {"status": "ok", "detail": "code-map is fresh"},
    )
    monkeypatch.setattr(
        cli_module,
        "_memory_mcp_tool_status",
        lambda: {"status": "ok", "detail": "15 read-only MCP tool(s) exposed"},
    )

    assert cli_module.main(["memory", "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warn"
    assert payload["providers"]["names"] == ["redis", "fleet"]
    assert payload["redis"]["status"] == "ok"
    assert payload["code_memory"]["status"] == "warn"


def test_memory_doctor_defaults_to_doctor_subcommand(
    cli_module, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_unified_memory_doctor",
        lambda: {
            "status": "ok",
            "providers": {"status": "ok", "names": ["redis"], "detail": "ok"},
            "redis": {"status": "ok", "detail": "ok"},
            "fleet_brain": {"status": "ok", "detail": "ok"},
            "code_memory": {"status": "ok", "detail": "ok"},
            "code_map": {"status": "ok", "detail": "ok"},
            "mcp_tools": {"status": "ok", "detail": "ok"},
        },
    )

    assert cli_module.main(["memory", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert cli_module.main(["memory", "--json", "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_memory_doctor_returns_failure_when_component_fails(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_unified_memory_doctor",
        lambda: {
            "status": "fail",
            "providers": {"status": "ok", "names": ["redis"], "detail": "ok"},
            "redis": {"status": "ok", "detail": "ok"},
            "fleet_brain": {"status": "fail", "detail": "db unreadable"},
            "code_memory": {"status": "ok", "detail": "ok"},
            "code_map": {"status": "ok", "detail": "ok"},
            "mcp_tools": {"status": "ok", "detail": "ok"},
        },
    )

    assert cli_module.main(["memory", "doctor"]) == 1


def test_memory_provider_chain_treats_null_as_disabled(cli_module) -> None:
    status = cli_module._memory_provider_chain_status({"ALFRED_MEMORY_PROVIDERS": "null"})

    assert status["status"] == "disabled"
    assert status["disabled"] is True
    assert status["enabled_names"] == []


def test_memory_code_map_status_warns_when_stale(cli_module, tmp_path: Path) -> None:
    code_map = tmp_path / "code-map.json"
    code_map.write_text(
        json.dumps({"generated_at": "2000-01-01T00:00:00Z", "repos": {}, "contract_drift": []}),
        encoding="utf-8",
    )

    status = cli_module._memory_code_map_status(code_map)

    assert status["status"] == "warn"
    assert status["exists"] is True
    assert "stale" in status["detail"]


def test_doctor_command_forwards_to_doctor_script(
    cli_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakeProcess)

    assert cli_module.main(["doctor", "--dev", "--lifecycle"]) == 0
    assert calls == [["bash", str(REPO_ROOT / "bin" / "doctor.sh"), "--dev", "--lifecycle"]]


def _engine_readiness(
    cli_module,
    engine: str,
    *,
    ready: bool,
    state: str,
    detail: str,
    version: str | None = None,
    failures: tuple[str, ...] | None = None,
):
    descriptor = cli_module.agent_runner.DEFAULT_ENGINE_REGISTRY.descriptor(engine)
    return cli_module.agent_runner.EngineProbeResult(
        descriptor=descriptor,
        installed=True,
        protocol_compatible=state not in {"missing", "incompatible"},
        ready=ready,
        state=state,
        detail=detail,
        binary=engine,
        version=version or f"{engine} 9.9.9",
        failures=failures if failures is not None else (() if ready else (state,)),
    )


def test_engine_doctor_discovers_systemd_roster_without_agents_conf(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    (systemd_dir / "alfred.reviewer.service").write_text(
        "[Service]\nExecStart=/opt/alfred/bin/agent-launch /opt/alfred/bin/reviewer.py\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFRED_LAUNCH_DIR", str(tmp_path / "missing-launchd"))
    monkeypatch.setenv("ALFRED_SYSTEMD_USER_DIR", str(systemd_dir))
    monkeypatch.setattr(
        cli_module.agent_runner,
        "agent_engine",
        lambda _agent, *, default: "codex",
    )

    assert cli_module._configured_engine_selections() == {"codex": ("reviewer",)}


def test_engine_doctor_discovers_launchd_roster_without_agents_conf(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launch_dir = tmp_path / "launch-agents"
    launch_dir.mkdir()
    (launch_dir / "alfred.reviewer.plist").write_bytes(
        plistlib.dumps(
            {
                "Label": "alfred.reviewer",
                "ProgramArguments": [
                    "/opt/alfred/bin/agent-launch",
                    "/opt/alfred/bin/reviewer.py",
                ],
            }
        )
    )
    monkeypatch.setenv("ALFRED_LAUNCH_DIR", str(launch_dir))
    monkeypatch.setattr(
        cli_module.agent_runner,
        "agent_engine",
        lambda _agent, *, default: "codex",
    )

    assert cli_module._configured_engine_selections() == {"codex": ("reviewer",)}


@pytest.mark.parametrize(
    "label",
    (
        "alfred.bad/path",
        "alfred.foo.x/../../owned",
        "alfred.foo.../tmp/owned",
    ),
)
def test_configured_roster_rejects_unsafe_agents_conf_codenames(
    cli_module,
    tmp_path: Path,
    label: str,
) -> None:
    home = Path(os.environ["ALFRED_HOME"])
    conf = home / "launchd" / "agents.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        f"{label}\treviewer.py\tinterval:3600\tno\t\tReviewer\n",
        encoding="utf-8",
    )

    assert cli_module._configured_agent_records() == []


@pytest.mark.parametrize(
    "label",
    (
        "alfred.bad/path",
        "alfred.foo.x/../../owned",
        "alfred.foo.../tmp/owned",
    ),
)
def test_scheduler_roster_rejects_unsafe_launchd_codenames(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
) -> None:
    launch_dir = tmp_path / "launch-agents"
    launch_dir.mkdir()
    (launch_dir / "unsafe.plist").write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [
                    "/opt/alfred/bin/agent-launch",
                    "/opt/alfred/bin/reviewer.py",
                ],
            }
        )
    )
    monkeypatch.setenv("ALFRED_LAUNCH_DIR", str(launch_dir))

    assert cli_module._scheduler_agent_records() == []


@pytest.mark.parametrize("agent", ("bad/path", "x/../../owned", "/tmp/owned"))
def test_engine_state_file_rejects_unsafe_codenames(cli_module, agent: str) -> None:
    with pytest.raises(ValueError, match="agent codename"):
        cli_module._engine_state_file(agent)


def test_engine_state_file_rejects_a_symlink_outside_engines_root(
    cli_module,
    tmp_path: Path,
) -> None:
    engines_root = cli_module.agent_runner.STATE_ROOT / "engines"
    engines_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (engines_root / "reviewer").symlink_to(outside / "reviewer")

    with pytest.raises(ValueError, match="engines root"):
        cli_module._engine_state_file("reviewer")


def test_engine_doctor_excludes_runtime_disabled_opt_in_agents(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "label": "alfred.architect",
            "script": "architect.py",
            "disabled": "no",
        },
        {
            "label": "alfred.reviewer",
            "script": "reviewer.py",
            "disabled": "no",
        },
    ]
    enabled_calls: list[tuple[str, bool]] = []

    def is_enabled(codename: str, *, default: bool) -> bool:
        enabled_calls.append((codename, default))
        return default

    monkeypatch.setattr(cli_module, "_configured_agent_records", lambda: records)
    monkeypatch.setattr(cli_module.agent_runner, "is_agent_enabled", is_enabled)
    monkeypatch.setattr(
        cli_module.agent_runner,
        "agent_engine",
        lambda _agent, *, default: default,
    )

    assert cli_module._configured_engine_selections() == {"hybrid": ("reviewer",)}
    assert enabled_calls == [("architect", False), ("reviewer", True)]


def test_engine_doctor_fails_when_selected_engine_is_not_registry_ready(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_out = _engine_readiness(
        cli_module,
        "codex",
        ready=False,
        state="auth_required",
        detail="Codex is installed but is not signed in.",
    )
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"codex": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "probe_engine",
        lambda *_args, **_kwargs: signed_out,
    )

    assert cli_module.main(["engine", "doctor"]) == 1
    output = capsys.readouterr().out
    assert "doctor: checking configured engine readiness" in output
    assert "codex    [reviewer] FAIL (auth_required)" in output
    assert "Codex is installed but is not signed in." in output


def test_engine_doctor_uses_ready_codex_for_hybrid_capability_gap(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_module = cli_module.scheduler._load()
    results = {
        "claude": _engine_readiness(
            cli_module,
            "claude",
            ready=False,
            state="incompatible",
            detail="Claude Code does not expose Alfred's required CLI protocol.",
        ),
        "codex": _engine_readiness(
            cli_module,
            "codex",
            ready=True,
            state="ready",
            detail="Codex is compatible and signed in.",
        ),
    }
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"hybrid": ("planner", "senior-dev")},
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "probe_engine",
        lambda descriptor, **_kwargs: results[descriptor.id],
    )
    monkeypatch.setattr(
        scheduler_module,
        "manager_environment_lookup",
        lambda _name: scheduler_module.ManagerEnvironmentLookup(available=True),
    )

    assert cli_module.main(["engine", "doctor"]) == 0
    output = capsys.readouterr().out
    assert "hybrid   [planner, senior-dev] OK (ready_via_codex)" in output
    assert "Codex fallback is compatible and signed in." in output


def test_engine_doctor_does_not_hide_hybrid_claude_auth_failure(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_module = cli_module.scheduler._load()
    probes: list[str] = []
    signed_out = _engine_readiness(
        cli_module,
        "claude",
        ready=False,
        state="auth_required",
        detail="Claude Code is installed but is not signed in.",
    )

    def probe(descriptor, **_kwargs):
        probes.append(descriptor.id)
        if descriptor.id != "claude":
            raise AssertionError("scheduled hybrid dispatch does not fall back on auth failure")
        return signed_out

    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"hybrid": ("senior-dev",)},
    )
    monkeypatch.setattr(cli_module.agent_runner, "probe_engine", probe)
    monkeypatch.setattr(
        scheduler_module,
        "manager_environment_lookup",
        lambda _name: scheduler_module.ManagerEnvironmentLookup(available=True),
    )

    assert cli_module.main(["engine", "doctor"]) == 1
    output = capsys.readouterr().out
    assert "hybrid   [senior-dev] FAIL (auth_required)" in output
    assert probes == ["claude"]


def test_engine_doctor_probes_selected_scheduler_claude_profile(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_profile = tmp_path / ".claude-secondary"
    received_environments: list[dict[str, str]] = []
    ready = _engine_readiness(
        cli_module,
        "claude",
        ready=True,
        state="ready",
        detail="Claude Code is compatible and signed in.",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-shell"))
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"claude": ("reviewer",)},
    )
    monkeypatch.setattr(cli_module, "_current_claude_dir", lambda: str(selected_profile))

    def probe(_descriptor, **kwargs):
        received_environments.append(dict(kwargs["environ"]))
        return ready

    monkeypatch.setattr(cli_module.agent_runner, "probe_engine", probe)

    assert cli_module.main(["engine", "doctor"]) == 0
    assert capsys.readouterr().out
    assert received_environments[0]["CLAUDE_CONFIG_DIR"] == str(selected_profile)


def test_engine_doctor_probes_static_claude_profile_without_manager_override(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    static_profile = tmp_path / ".claude-static"
    received_environments: list[dict[str, str]] = []
    ready = _engine_readiness(
        cli_module,
        "claude",
        ready=True,
        state="ready",
        detail="Claude Code is compatible and signed in.",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-shell"))
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"claude": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module.scheduler._load(),
        "manager_environment_lookup",
        lambda _name: cli_module.scheduler._load().ManagerEnvironmentLookup(available=True),
    )
    monkeypatch.setattr(
        cli_module,
        "_read_env_values",
        lambda _path: {"CLAUDE_CONFIG_DIR": str(static_profile)},
    )

    def probe(_descriptor, **kwargs):
        received_environments.append(dict(kwargs["environ"]))
        return ready

    monkeypatch.setattr(cli_module.agent_runner, "probe_engine", probe)

    assert cli_module.main(["engine", "doctor"]) == 0
    assert capsys.readouterr().out
    assert received_environments[0]["CLAUDE_CONFIG_DIR"] == str(static_profile)


def test_engine_doctor_fails_closed_when_profile_lookup_fails(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_module = cli_module.scheduler._load()
    lookups = 0

    def unavailable_profile(_name):
        nonlocal lookups
        lookups += 1
        return scheduler_module.ManagerEnvironmentLookup()

    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"claude": ("reviewer",), "hybrid": ("senior-dev",)},
    )
    monkeypatch.setattr(
        scheduler_module,
        "manager_environment_lookup",
        unavailable_profile,
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "probe_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("engine must not be probed with an unknown profile")
        ),
    )

    assert cli_module.main(["engine", "doctor"]) == 1
    output = capsys.readouterr().out
    assert output.count("profile_lookup_failed") == 2
    assert output.count("Could not read the scheduler-selected Claude profile") == 2
    assert lookups == 1


def test_engine_doctor_renders_unsupported_version_remediation(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_module = cli_module.scheduler._load()
    unsupported = _engine_readiness(
        cli_module,
        "claude",
        ready=False,
        state="incompatible",
        detail="Claude Code does not expose Alfred's required CLI protocol.",
        version="Claude Code 2.1.40",
        failures=("unsupported_version",),
    )
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"claude": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "probe_engine",
        lambda *_args, **_kwargs: unsupported,
    )
    monkeypatch.setattr(
        scheduler_module,
        "manager_environment_lookup",
        lambda _name: scheduler_module.ManagerEnvironmentLookup(available=True),
    )

    assert cli_module.main(["engine", "doctor"]) == 1
    output = capsys.readouterr().out
    assert "Installed version: Claude Code 2.1.40." in output
    assert "Minimum supported version: 2.1.41." in output
    assert "Upgrade Claude Code, then rerun `alfred engine doctor`." in output


def test_auth_status_uses_scheduler_profile_claude_readiness(
    cli_module,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_profile = tmp_path / ".claude-secondary"
    received_environments: list[dict[str, str]] = []
    signed_out = _engine_readiness(
        cli_module,
        "claude",
        ready=False,
        state="auth_required",
        detail="Claude Code is installed but is not signed in.",
    )
    monkeypatch.setattr(cli_module, "_current_claude_dir", lambda: str(selected_profile))
    monkeypatch.setattr(cli_module, "_claude_status", lambda: 0)
    monkeypatch.setattr(cli_module, "_codex_status", lambda: 0)
    monkeypatch.setattr(cli_module, "_opencode_status", lambda: 0)

    def probe(_descriptor, **kwargs):
        received_environments.append(dict(kwargs["environ"]))
        return signed_out

    monkeypatch.setattr(cli_module.agent_runner, "probe_engine", probe)

    assert cli_module.cmd_auth(argparse.Namespace(auth_command="status")) == 1
    assert received_environments[0]["CLAUDE_CONFIG_DIR"] == str(selected_profile)
    assert "claude readiness: auth_required" in capsys.readouterr().out


def test_auth_status_propagates_opencode_readiness_failure(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_claude_readiness_status", lambda: 0)
    monkeypatch.setattr(cli_module, "_codex_status", lambda: 0)
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"opencode": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module,
        "_opencode_status",
        lambda: calls.append("opencode") or 1,
    )

    assert cli_module.cmd_auth(argparse.Namespace(auth_command="status")) == 1
    assert calls == ["opencode"]


def test_auth_status_reports_optional_opencode_without_failing(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_claude_readiness_status", lambda: 0)
    monkeypatch.setattr(cli_module, "_codex_status", lambda: 0)
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"hybrid": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module,
        "_opencode_status",
        lambda: calls.append("opencode") or 1,
    )

    assert cli_module.cmd_auth(argparse.Namespace(auth_command="status")) == 0
    assert calls == ["opencode"]


def test_opencode_probe_uses_read_only_adapter(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    received: dict[str, object] = {}

    def invoke(prompt: str, **kwargs):
        received["prompt"] = prompt
        received.update(kwargs)
        return SimpleNamespace(
            success=True,
            result_text="OPENCODE_PROBE_OK",
            error_message=None,
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module.agent_runner, "opencode_invoke", invoke)

    assert cli_module._probe_opencode() == 0
    assert received == {
        "prompt": "Reply with exactly: OPENCODE_PROBE_OK",
        "workdir": REPO_ROOT,
        "agent": "opencode-probe",
        "timeout": 90,
        "model": None,
        "allow_writes": False,
    }


def test_auth_probe_skips_unselected_opencode(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(cli_module, "_probe_claude_auth", lambda: 0)
    monkeypatch.setattr(cli_module, "_probe_codex", lambda: 0)
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"hybrid": ("reviewer",)},
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_opencode",
        lambda: pytest.fail("optional OpenCode probe must not run"),
    )

    assert cli_module.cmd_auth(argparse.Namespace(auth_command="probe")) == 0
    assert "OpenCode probe: skipped" in capsys.readouterr().out


def test_auth_probe_checks_distinct_selected_opencode_models(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str | None] = []
    selected_models = {
        "reviewer": "anthropic/claude-sonnet-4-5",
        "senior-dev": "openai/gpt-5.4",
        "fixer": "anthropic/claude-sonnet-4-5",
    }
    monkeypatch.setattr(cli_module, "_probe_claude_auth", lambda: 0)
    monkeypatch.setattr(cli_module, "_probe_codex", lambda: 0)
    monkeypatch.setattr(
        cli_module,
        "_configured_engine_selections",
        lambda: {"opencode": tuple(selected_models)},
    )
    monkeypatch.setattr(
        cli_module.agent_runner,
        "agent_model",
        lambda agent, engine: selected_models[agent] if engine == "opencode" else None,
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_opencode",
        lambda *, model=None: probed.append(model) or 0,
    )

    assert cli_module.cmd_auth(argparse.Namespace(auth_command="probe")) == 0
    assert probed == ["anthropic/claude-sonnet-4-5", "openai/gpt-5.4"]


def test_launchctl_timeout_returns_controlled_status(cli_module, monkeypatch):
    def time_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr="partial")

    monkeypatch.setattr(cli_module.subprocess, "run", time_out)

    result = cli_module._launchctl(["list"])

    assert result.returncode == 124
    assert "partial" in result.stderr
    assert "timed out" in result.stderr


def test_capabilities_command_emits_json(
    cli_module, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server import setup as setup_mod

    payload = {
        "version": 1,
        "summary": {"ready": 1, "actionable": 0, "disabled": 0, "total": 1},
        "capabilities": [
            {
                "key": "code_graph",
                "title": "Code graph memory",
                "category": "memory",
                "recommended": True,
                "state": "ready",
                "installed": True,
                "enabled": True,
                "detail": "ready",
                "detected": {},
                "install_hint": "none",
                "source": {"source": "DeusData/codebase-memory-mcp"},
            }
        ],
    }
    monkeypatch.setattr(setup_mod, "capability_status", lambda: payload)

    assert cli_module.main(["capabilities", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_capabilities_command_import_survives_unresolvable_home(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    (codex_home / "skills" / "gstack").mkdir(parents=True)
    (claude_home / "skills").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env = {
        **os.environ,
        "ALFRED_HOME": str(runtime),
        "CODEX_HOME": str(codex_home),
        "CLAUDE_HOME": str(claude_home),
        "PYTHONPATH": str(LIB),
    }
    env.pop("HOME", None)
    code = f"""
import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

pathlib.Path.home = staticmethod(
    lambda: (_ for _ in ()).throw(RuntimeError("no home"))
)
loader = SourceFileLoader("alfred_cli_cold_capabilities", {str(BIN)!r})
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = mod
spec.loader.exec_module(mod)
raise SystemExit(mod.main(["capabilities", "--json"]))
"""

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    skills = {item["key"]: item for item in payload["capabilities"]}["engineering_skills"]
    assert skills["state"] == "ready"
    assert skills["detected"]["paths"] == [str(codex_home / "skills" / "gstack")]


def test_claude_home_does_not_override_primary_auth_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    claude_home = tmp_path / "skill-claude-home"
    home.mkdir()
    runtime.mkdir()
    claude_home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "ALFRED_HOME": str(runtime),
        "CLAUDE_HOME": str(claude_home),
        "PYTHONPATH": str(LIB),
    }
    code = f"""
import importlib.util
import sys
from importlib.machinery import SourceFileLoader

loader = SourceFileLoader("alfred_cli_claude_home_auth", {str(BIN)!r})
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = mod
spec.loader.exec_module(mod)
print(mod.PRIMARY_CLAUDE_DIR)
"""

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(home / ".claude")

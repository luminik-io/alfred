"""Contract tests for coding-engine discovery and readiness."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _runner(outputs: dict[tuple[str, ...], tuple[int, str, str]]):
    calls: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        key = tuple(command[1:])
        calls.append(key)
        returncode, stdout, stderr = outputs[key]
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run, calls


def test_default_registry_separates_dispatchable_and_candidate_engines(fresh_agent_runner):
    ar = fresh_agent_runner

    assert ar.DEFAULT_ENGINE_REGISTRY.dispatchable_ids == {"claude", "codex", "opencode"}
    assert [row.id for row in ar.ENGINE_DESCRIPTORS] == [
        "claude",
        "codex",
        "opencode",
        "cline",
    ]
    assert ar.DEFAULT_ENGINE_REGISTRY.descriptor("CLAUDE").display_name == "Claude Code"
    with pytest.raises(ValueError, match="unknown engine"):
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("other")


def test_registry_rejects_duplicate_and_unsafe_ids(fresh_agent_runner):
    ar = fresh_agent_runner
    descriptor = ar.ENGINE_DESCRIPTORS[0]

    with pytest.raises(ValueError, match="unique"):
        ar.EngineRegistry((descriptor, descriptor))
    with pytest.raises(ValueError, match="invalid engine id"):
        ar.EngineDescriptor(
            id="../unsafe",
            display_name="Unsafe",
            binary_env="UNSAFE_BIN",
            default_binary="unsafe",
            capabilities=frozenset(),
            protocol_commands=(),
        )


def test_probe_result_rejects_unknown_readiness_state(fresh_agent_runner):
    ar = fresh_agent_runner

    with pytest.raises(ValueError, match="not a valid EngineProbeState"):
        ar.EngineProbeResult(
            descriptor=ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
            installed=True,
            protocol_compatible=True,
            ready=False,
            state="future_state",
            detail="unknown",
            binary="/bin/claude",
            version="test",
        )


def test_dispatchable_probe_requires_protocol_and_auth(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")
    runner, calls = _runner(
        {
            ("--version",): (0, "Claude Code 2.1.41\n", ""),
            ("auth", "status"): (0, "private account details", ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
        environ={"CLAUDE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is True
    assert result.state == "ready"
    assert result.version == "Claude Code 2.1.41"
    assert result.as_dict()["minimum_version"] == "2.1.41"
    assert result.failures == ()
    assert calls == [("--version",), ("auth", "status")]
    assert "private account" not in str(result.as_dict())


def test_claude_probe_does_not_depend_on_incomplete_help(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        args = tuple(command[1:])
        calls.append(args)
        if args == ("--version",):
            return subprocess.CompletedProcess(command, 0, "2.1.41 (Claude Code)\n", "")
        if args == ("auth", "status"):
            return subprocess.CompletedProcess(command, 0, "signed in\n", "")
        raise AssertionError(f"help output must not gate Claude readiness: {args}")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
        environ={"CLAUDE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is True
    assert calls == [("--version",), ("auth", "status")]


def test_claude_probe_rejects_version_without_auth_status(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")
    runner, calls = _runner({("--version",): (0, "Claude Code 2.1.40\n", "")})

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
        environ={"CLAUDE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is False
    assert result.state == "incompatible"
    assert result.failures == ("unsupported_version",)
    assert calls == [("--version",)]


def test_probe_process_receives_only_non_secret_runtime_context(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    received_environments: list[dict[str, str]] = []

    def runner(command, **kwargs):
        received_environments.append(kwargs["env"])
        args = tuple(command[1:])
        outputs = {
            ("--version",): "codex 1.2.3\n",
            ("exec", "--help"): (
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n"
            ),
            ("login", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[args], "")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={
            "CODEX_BIN": str(binary),
            "CODEX_HOME": str(tmp_path / "codex-home"),
            "CODEX_ACCESS_TOKEN": "codex-automation-token",
            "CODEX_CA_CERTIFICATE": str(tmp_path / "codex-ca.pem"),
            "SSL_CERT_FILE": str(tmp_path / "corporate-ca.pem"),
            "GITHUB_TOKEN": "must-not-cross-probe-boundary",
            "SLACK_BOT_TOKEN": "must-not-cross-probe-boundary",
            "AWS_SECRET_ACCESS_KEY": "must-not-cross-probe-boundary",
            "OPENAI_API_KEY": "engine-specific-auth-context",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TERM": "xterm-256color",
        },
        runner=runner,
        use_cache=False,
    )

    assert result.ready is True
    assert received_environments == [
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TERM": "xterm-256color",
        },
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TERM": "xterm-256color",
        },
        {
            "CODEX_ACCESS_TOKEN": "codex-automation-token",
            "CODEX_CA_CERTIFICATE": str(tmp_path / "codex-ca.pem"),
            "CODEX_HOME": str(tmp_path / "codex-home"),
            "SSL_CERT_FILE": str(tmp_path / "corporate-ca.pem"),
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TERM": "xterm-256color",
        },
    ]
    for child_env in received_environments:
        assert "GITHUB_TOKEN" not in child_env
        assert "SLACK_BOT_TOKEN" not in child_env
        assert "AWS_SECRET_ACCESS_KEY" not in child_env
        assert "OPENAI_API_KEY" not in child_env


def test_codex_api_key_never_bypasses_cli_auth_probe(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    calls: list[tuple[str, ...]] = []

    def runner(command, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        args = tuple(command[1:])
        calls.append(args)
        outputs = {
            ("--version",): "codex 1.2.3\n",
            ("exec", "--help"): (
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n"
            ),
            ("login", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[args], "")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={
            "CODEX_BIN": str(binary),
            "OPENAI_API_KEY": "configured-api-key",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
        runner=runner,
        use_cache=False,
    )

    assert result.ready is True
    assert calls == [("--version",), ("exec", "--help"), ("login", "status")]


def test_invalid_codex_api_key_cannot_make_engine_ready(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")

    def runner(command, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        args = tuple(command[1:])
        outputs = {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (
                0,
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n",
                "",
            ),
            ("login", "status"): (1, "", "not signed in"),
        }
        returncode, stdout, stderr = outputs[args]
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={
            "CODEX_BIN": str(binary),
            "OPENAI_API_KEY": "invalid-or-revoked-key",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
        runner=runner,
        use_cache=False,
    )

    assert result.ready is False
    assert result.state == "auth_required"


def test_claude_auth_probe_receives_only_claude_auth_context(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")
    received_environments: list[dict[str, str]] = []

    def runner(command, **kwargs):
        received_environments.append(kwargs["env"])
        args = tuple(command[1:])
        outputs = {
            ("--version",): "Claude Code 2.1.41\n",
            ("auth", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[args], "")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
        environ={
            "CLAUDE_BIN": str(binary),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-profile"),
            "CLAUDE_CODE_OAUTH_TOKEN": "claude-auth-token",
            "OPENAI_API_KEY": "must-not-cross-engine-boundary",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
        runner=runner,
        use_cache=False,
    )

    assert result.ready is True
    assert "CLAUDE_CONFIG_DIR" not in received_environments[0]
    assert received_environments[1]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-profile")
    assert received_environments[1]["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-auth-token"
    assert all("OPENAI_API_KEY" not in child_env for child_env in received_environments)


def test_probe_fails_closed_on_protocol_drift(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    runner, calls = _runner(
        {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (0, "--sandbox --cd\n", ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.installed is True
    assert result.protocol_compatible is False
    assert result.ready is False
    assert result.state == "incompatible"
    assert result.failures == ("protocol_mismatch",)
    assert ("login", "status") not in calls


def test_protocol_probe_transport_failure_is_retryable_and_not_cached(
    fresh_agent_runner, tmp_path: Path
):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    calls = 0

    def runner(command, **_kwargs):
        nonlocal calls
        calls += 1
        args = tuple(command[1:])
        if calls == 1:
            raise subprocess.TimeoutExpired(command, timeout=4)
        outputs = {
            ("--version",): "codex 1.2.3\n",
            ("exec", "--help"): (
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n"
            ),
            ("login", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[args], "")

    failed = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
    )
    ready = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
    )

    assert failed.ready is False
    assert failed.state == "probe_failed"
    assert failed.failures == ("protocol_probe_failed",)
    assert ready.ready is True
    assert calls == 4


@pytest.mark.parametrize(
    "missing_marker",
    (
        "--output-last-message",
        "--sandbox",
        "--cd",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ephemeral",
        "-c",
        "--model",
        "--add-dir",
        "--dangerously-bypass-approvals-and-sandbox",
    ),
)
def test_codex_probe_requires_every_dispatch_flag(
    fresh_agent_runner,
    tmp_path: Path,
    missing_marker: str,
):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    dispatch_flags = {
        "--output-last-message",
        "--sandbox",
        "--cd",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ephemeral",
        "-c",
        "--model",
        "--add-dir",
        "--dangerously-bypass-approvals-and-sandbox",
    }
    help_output = " ".join(sorted(dispatch_flags - {missing_marker}))
    runner, calls = _runner(
        {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (0, help_output, ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.state == "incompatible"
    assert result.failures == ("protocol_mismatch",)
    assert ("login", "status") not in calls


def test_codex_probe_does_not_treat_other_config_or_directory_flags_as_short_config(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    runner, calls = _runner(
        {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (
                0,
                "--output-last-message --sandbox -C --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral --strict-config --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n",
                "",
            ),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.state == "incompatible"
    assert result.failures == ("protocol_mismatch",)
    assert ("login", "status") not in calls


def test_probe_reports_auth_required_without_leaking_output(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    runner, _calls = _runner(
        {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (
                0,
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n",
                "",
            ),
            ("login", "status"): (1, "private account details", "expired token"),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.protocol_compatible is True
    assert result.ready is False
    assert result.state == "auth_required"
    assert result.failures == ("auth_required",)
    assert "private account" not in str(result.as_dict())
    assert "expired token" not in str(result.as_dict())


def test_auth_probe_transport_failure_is_not_reported_as_signed_out(
    fresh_agent_runner, tmp_path: Path
):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")

    def runner(command, **_kwargs):
        args = tuple(command[1:])
        if args == ("--version",):
            return subprocess.CompletedProcess(command, 0, "Claude Code 2.1.41\n", "")
        raise subprocess.TimeoutExpired(command, timeout=4)

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
        environ={"CLAUDE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is False
    assert result.state == "probe_failed"
    assert result.failures == ("auth_probe_failed",)


def test_opencode_probe_requires_current_protocol_and_stored_auth(
    fresh_agent_runner, tmp_path: Path
):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "opencode")
    runner, _calls = _runner(
        {
            ("--version",): (0, "opencode 1.18.18\n", ""),
            ("--help",): (0, "--pure\n", ""),
            ("run", "--help"): (0, "--format --model --dir --agent\n", ""),
            ("auth", "list"): (0, "Credentials\n1 credential\n", ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode"),
        environ={"OPENCODE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.installed is True
    assert result.protocol_compatible is True
    assert result.ready is True
    assert result.state == "ready"
    assert result.failures == ()


def test_opencode_probe_rejects_empty_credential_store(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "opencode")
    runner, calls = _runner(
        {
            ("--version",): (0, "opencode 1.18.18\n", ""),
            ("--help",): (0, "--pure\n", ""),
            ("run", "--help"): (0, "--format --model --dir --agent\n", ""),
            ("auth", "list"): (0, "Credentials\n0 credentials\n", ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode"),
        environ={"OPENCODE_BIN": str(binary), "PATH": "", "HOME": str(tmp_path)},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is False
    assert result.state == "auth_required"
    assert result.failures == ("auth_required",)
    assert calls == [("--version",), ("--help",), ("run", "--help"), ("auth", "list")]


def test_opencode_probe_rejects_pre_contract_version(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "opencode")
    runner, calls = _runner({("--version",): (0, "opencode 1.18.17\n", "")})

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode"),
        environ={"OPENCODE_BIN": str(binary), "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.ready is False
    assert result.state == "incompatible"
    assert result.failures == ("unsupported_version",)
    assert calls == [("--version",)]


def test_inventory_probes_dispatchable_opencode_protocol_and_auth(
    fresh_agent_runner, tmp_path: Path
):
    ar = fresh_agent_runner
    candidate = tmp_path / "opencode"
    candidate.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'opencode 1.18.18\'; exit 0; fi\n'
        'if [ "$1" = "--help" ]; then echo \'--pure\'; exit 0; fi\n'
        'if [ "$1 $2" = "run --help" ]; then echo \'--format --model --dir --agent\'; exit 0; fi\n'
        'if [ "$1 $2" = "auth list" ]; then echo \'1 credential\'; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    candidate.chmod(0o755)

    rows = ar.DEFAULT_ENGINE_REGISTRY.inventory(
        environ={"OPENCODE_BIN": str(candidate), "PATH": ""},
        search_path="",
        use_cache=False,
    )

    opencode = next(row for row in rows if row["name"] == "opencode")
    assert opencode["installed"] is True
    assert opencode["protocol_compatible"] is True
    assert opencode["ready"] is True
    assert opencode["state"] == "ready"
    assert opencode["version"] == "opencode 1.18.18"


def test_probe_returns_a_canonical_absolute_executable(
    fresh_agent_runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    ar = fresh_agent_runner
    tools = tmp_path / "tools"
    tools.mkdir()
    target = _executable(tools / "opencode-real")
    link = tools / "opencode"
    link.symlink_to(target)
    monkeypatch.chdir(tmp_path)
    runner, _calls = _runner(
        {
            ("--version",): (0, "opencode 1.18.18\n", ""),
            ("--help",): (0, "--pure\n", ""),
            ("run", "--help"): (0, "--format --model --dir --agent\n", ""),
            ("auth", "list"): (0, "1 credential\n", ""),
        }
    )

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode"),
        environ={"OPENCODE_BIN": "./tools/opencode", "PATH": ""},
        runner=runner,
        use_cache=False,
    )

    assert result.binary == str(target.resolve())


def test_inventory_probes_dispatchable_engines_concurrently(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    claude = _executable(tmp_path / "claude")
    codex = _executable(tmp_path / "codex")
    first_commands = threading.Barrier(2)

    def runner(command, **_kwargs):
        args = tuple(command[1:])
        if args == ("--version",):
            first_commands.wait(timeout=5)
        outputs = {
            (str(claude), "--version"): "Claude Code 2.1.41\n",
            (str(claude), "auth", "status"): "signed in\n",
            (str(codex), "--version"): "codex 1.2.3\n",
            (str(codex), "exec", "--help"): (
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n"
            ),
            (str(codex), "login", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[tuple(command)], "")

    rows = ar.DEFAULT_ENGINE_REGISTRY.inventory(
        environ={
            "CLAUDE_BIN": str(claude),
            "CODEX_BIN": str(codex),
            "PATH": "",
        },
        search_path="",
        runner=runner,
        use_cache=False,
    )

    by_name = {row["name"]: row for row in rows}
    assert by_name["claude"]["ready"] is True
    assert by_name["codex"]["ready"] is True


@pytest.mark.parametrize(
    "unexpected_error",
    (
        ValueError("runner returned malformed output with private details"),
        RuntimeError("unexpected runner failure with private details"),
    ),
)
def test_inventory_isolates_unexpected_runner_exceptions(
    fresh_agent_runner,
    tmp_path: Path,
    unexpected_error: Exception,
):
    ar = fresh_agent_runner
    claude = _executable(tmp_path / "claude")
    codex = _executable(tmp_path / "codex")

    def runner(command, **_kwargs):
        if command[0] == str(codex):
            raise unexpected_error
        output = "Claude Code 2.1.41\n" if command[1:] == ["--version"] else "signed in\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    rows = ar.DEFAULT_ENGINE_REGISTRY.inventory(
        environ={
            "CLAUDE_BIN": str(claude),
            "CODEX_BIN": str(codex),
            "PATH": "",
        },
        search_path="",
        runner=runner,
        use_cache=False,
    )

    by_name = {row["name"]: row for row in rows}
    assert by_name["claude"]["ready"] is True
    assert by_name["codex"]["installed"] is True
    assert by_name["codex"]["ready"] is False
    assert by_name["codex"]["state"] == "probe_failed"
    assert by_name["codex"]["failures"] == ["unexpected_probe_failure"]
    assert "private details" not in str(by_name["codex"])


def test_inventory_keeps_a_ready_engine_when_another_probe_stalls(
    fresh_agent_runner, tmp_path: Path
):
    ar = fresh_agent_runner
    claude = _executable(tmp_path / "claude")
    codex = _executable(tmp_path / "codex")

    def runner(command, **kwargs):
        if command[0] == str(codex):
            raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])
        output = "Claude Code 2.1.41\n" if command[1:] == ["--version"] else "signed in\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    rows = ar.DEFAULT_ENGINE_REGISTRY.inventory(
        environ={
            "CLAUDE_BIN": str(claude),
            "CODEX_BIN": str(codex),
            "PATH": "",
        },
        search_path="",
        runner=runner,
        use_cache=False,
    )

    by_name = {row["name"]: row for row in rows}
    assert by_name["claude"]["ready"] is True
    assert by_name["codex"]["ready"] is False
    assert by_name["codex"]["state"] == "probe_failed"


@pytest.mark.skipif(os.name == "nt", reason="Alfred schedules agents on macOS and Linux")
def test_probe_timeout_stops_parent_and_reaps_helpers_before_parent(
    fresh_agent_runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner import engine_registry as registry

    events: list[tuple[str, int, int] | tuple[str]] = []

    class ProbeProcess:
        pid = 100
        stdout = None
        stderr = None

        def poll(self):
            return None

        def communicate(self, *, timeout: float):
            assert timeout == 1
            events.append(("communicate",))
            return ("", "")

        def kill(self):
            events.append(("kill",))

    monkeypatch.setattr(registry, "_process_group_member_pids", lambda _group: (100, 101, 102))
    monkeypatch.setattr(
        registry.os,
        "kill",
        lambda pid, sig: events.append(("signal", pid, sig)),
    )

    registry._terminate_probe_process_group(ProbeProcess())

    assert events == [
        ("signal", 100, signal.SIGSTOP),
        ("signal", 101, signal.SIGKILL),
        ("signal", 102, signal.SIGKILL),
        ("signal", 100, signal.SIGCONT),
        ("communicate",),
    ]


@pytest.mark.skipif(os.name == "nt", reason="Alfred schedules agents on macOS and Linux")
def test_production_probe_timeout_terminates_helper_process_group(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    child_pid_path = tmp_path / "helper-child.pid"
    binary = tmp_path / "claude"
    binary.write_text(
        "#!/bin/sh\n"
        "sleep 30 >/dev/null 2>&1 &\n"
        "child=$!\n"
        f"printf '%s\\n' \"$child\" > {child_pid_path}\n"
        'wait "$child"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    child_pid: int | None = None

    try:
        result = ar.probe_engine(
            ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude"),
            environ={"CLAUDE_BIN": str(binary), "PATH": os.environ.get("PATH", "")},
            use_cache=False,
            deadline=time.monotonic() + 0.75,
        )

        assert result.state == "probe_failed"
        child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
        reap_deadline = time.monotonic() + 2
        while time.monotonic() < reap_deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("probe helper child survived the timeout")
    finally:
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_probe_stops_starting_commands_after_shared_deadline(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    codex = _executable(tmp_path / "codex")
    now = 0.0
    calls: list[tuple[str, ...]] = []
    timeouts: list[float] = []

    def clock() -> float:
        return now

    def runner(command, **kwargs):
        nonlocal now
        calls.append(tuple(command[1:]))
        timeouts.append(kwargs["timeout"])
        now += 5.0
        output = (
            "codex 1.2.3\n"
            if command[1:] == ["--version"]
            else (
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n"
            )
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={"CODEX_BIN": str(codex), "PATH": ""},
        runner=runner,
        use_cache=False,
        deadline=8.0,
        clock=clock,
    )

    assert result.state == "probe_failed"
    assert calls == [("--version",), ("exec", "--help")]
    assert timeouts == [4.0, 3.0]


def test_missing_binary_does_not_run_a_probe(fresh_agent_runner):
    ar = fresh_agent_runner

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("missing binaries must not be spawned")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("cline"),
        environ={"PATH": ""},
        runner=fail_runner,
        which=lambda *_args, **_kwargs: None,
        use_cache=False,
    )

    assert result.installed is False
    assert result.state == "missing"
    assert result.failures == ("missing_binary",)


def test_cached_protocol_still_rechecks_auth(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    ar.clear_engine_probe_cache()
    binary = _executable(tmp_path / "codex")
    auth_calls = 0
    protocol_calls = 0

    def runner(command, **_kwargs):
        nonlocal auth_calls, protocol_calls
        args = tuple(command[1:])
        if args == ("--version",):
            protocol_calls += 1
            return subprocess.CompletedProcess(command, 0, "codex 1.2.3\n", "")
        if args == ("exec", "--help"):
            protocol_calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n",
                "",
            )
        if args == ("login", "status"):
            auth_calls += 1
            return subprocess.CompletedProcess(command, 0 if auth_calls > 1 else 1, "", "")
        raise AssertionError(f"unexpected probe: {args}")

    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex")
    first = ar.probe_engine(
        descriptor,
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
    )
    second = ar.probe_engine(
        descriptor,
        environ={"CODEX_BIN": str(binary), "PATH": ""},
        runner=runner,
    )

    assert first.state == "auth_required"
    assert second.state == "ready"
    assert protocol_calls == 2
    assert auth_calls == 2


def test_inventory_reuses_fresh_auth_while_direct_probe_rechecks_it(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    ar.clear_engine_probe_cache()
    binary = _executable(tmp_path / "claude")
    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude")
    registry = ar.EngineRegistry((descriptor,))
    protocol_calls = 0
    auth_calls = 0

    def runner(command, **_kwargs):
        nonlocal auth_calls, protocol_calls
        args = tuple(command[1:])
        if args == ("--version",):
            protocol_calls += 1
            return subprocess.CompletedProcess(command, 0, "Claude Code 2.1.41\n", "")
        if args == ("auth", "status"):
            auth_calls += 1
            return subprocess.CompletedProcess(command, 0, "signed in\n", "")
        raise AssertionError(f"unexpected probe: {args}")

    options = {
        "environ": {"CLAUDE_BIN": str(binary), "PATH": ""},
        "search_path": "",
        "runner": runner,
    }
    first = registry.inventory(**options)
    second = registry.inventory(**options)
    direct = ar.probe_engine(descriptor, **options)

    assert first[0]["ready"] is True
    assert second[0]["ready"] is True
    assert direct.ready is True
    assert protocol_calls == 1
    assert auth_calls == 2


def test_claude_inventory_cache_is_scoped_to_auth_environment(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    ar.clear_engine_probe_cache()
    binary = _executable(tmp_path / "claude")
    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude")
    registry = ar.EngineRegistry((descriptor,))
    auth_profiles: list[str] = []

    def runner(command, **kwargs):
        args = tuple(command[1:])
        if args == ("--version",):
            return subprocess.CompletedProcess(command, 0, "Claude Code 2.1.41\n", "")
        if args == ("auth", "status"):
            profile = kwargs["env"]["CLAUDE_CONFIG_DIR"]
            auth_profiles.append(profile)
            return subprocess.CompletedProcess(
                command,
                0 if profile.endswith("ready") else 1,
                "",
                "",
            )
        raise AssertionError(f"unexpected probe: {args}")

    first = registry.inventory(
        environ={
            "CLAUDE_BIN": str(binary),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "ready"),
            "PATH": "",
        },
        search_path="",
        runner=runner,
    )
    second = registry.inventory(
        environ={
            "CLAUDE_BIN": str(binary),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "signed-out"),
            "PATH": "",
        },
        search_path="",
        runner=runner,
    )

    assert first[0]["state"] == "ready"
    assert second[0]["state"] == "auth_required"
    assert auth_profiles == [str(tmp_path / "ready"), str(tmp_path / "signed-out")]


def test_codex_inventory_cache_is_scoped_to_auth_environment(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    ar.clear_engine_probe_cache()
    binary = _executable(tmp_path / "codex")
    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex")
    registry = ar.EngineRegistry((descriptor,))
    auth_profiles: list[str] = []

    def runner(command, **kwargs):
        args = tuple(command[1:])
        if args == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex 1.2.3\n", "")
        if args == ("exec", "--help"):
            return subprocess.CompletedProcess(
                command,
                0,
                "--output-last-message --sandbox --cd --skip-git-repo-check "
                "--ignore-user-config --ephemeral -c --model --add-dir "
                "--dangerously-bypass-approvals-and-sandbox\n",
                "",
            )
        if args == ("login", "status"):
            profile = kwargs["env"]["CODEX_HOME"]
            auth_profiles.append(profile)
            return subprocess.CompletedProcess(
                command,
                0 if profile.endswith("ready") else 1,
                "",
                "",
            )
        raise AssertionError(f"unexpected probe: {args}")

    first = registry.inventory(
        environ={
            "CODEX_BIN": str(binary),
            "CODEX_HOME": str(tmp_path / "ready"),
            "PATH": "",
        },
        search_path="",
        runner=runner,
    )
    second = registry.inventory(
        environ={
            "CODEX_BIN": str(binary),
            "CODEX_HOME": str(tmp_path / "signed-out"),
            "PATH": "",
        },
        search_path="",
        runner=runner,
    )

    assert first[0]["state"] == "ready"
    assert second[0]["state"] == "auth_required"
    assert auth_profiles == [str(tmp_path / "ready"), str(tmp_path / "signed-out")]


def test_inventory_cache_distinguishes_missing_and_empty_auth_values(
    fresh_agent_runner,
    tmp_path: Path,
):
    ar = fresh_agent_runner
    ar.clear_engine_probe_cache()
    binary = _executable(tmp_path / "claude")
    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("claude")
    registry = ar.EngineRegistry((descriptor,))
    auth_calls = 0

    def runner(command, **kwargs):
        nonlocal auth_calls
        args = tuple(command[1:])
        if args == ("--version",):
            return subprocess.CompletedProcess(command, 0, "Claude Code 2.1.41\n", "")
        if args == ("auth", "status"):
            auth_calls += 1
            return subprocess.CompletedProcess(
                command,
                1 if "CLAUDE_CONFIG_DIR" in kwargs["env"] else 0,
                "",
                "",
            )
        raise AssertionError(f"unexpected probe: {args}")

    common = {"CLAUDE_BIN": str(binary), "PATH": ""}
    first = registry.inventory(
        environ=common,
        search_path="",
        runner=runner,
    )
    second = registry.inventory(
        environ={**common, "CLAUDE_CONFIG_DIR": ""},
        search_path="",
        runner=runner,
    )

    assert first[0]["state"] == "ready"
    assert second[0]["state"] == "auth_required"
    assert auth_calls == 2

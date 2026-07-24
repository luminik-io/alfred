"""Contract tests for coding-engine discovery and readiness."""

from __future__ import annotations

import subprocess
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

    assert ar.DEFAULT_ENGINE_REGISTRY.dispatchable_ids == {"claude", "codex"}
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


def test_dispatchable_probe_requires_protocol_and_auth(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "claude")
    runner, calls = _runner(
        {
            ("--version",): (0, "Claude Code 2.1.0\n", ""),
            ("--help",): (
                0,
                "--output-format --permission-mode --allowedTools\n",
                "",
            ),
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
    assert result.version == "Claude Code 2.1.0"
    assert result.failures == ()
    assert calls == [("--version",), ("--help",), ("auth", "status")]
    assert "private account" not in str(result.as_dict())


def test_probe_process_receives_only_non_secret_runtime_context(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    received_environments: list[dict[str, str]] = []

    def runner(command, **kwargs):
        received_environments.append(kwargs["env"])
        args = tuple(command[1:])
        outputs = {
            ("--version",): "codex 1.2.3\n",
            ("exec", "--help"): "--output-last-message --sandbox --cd\n",
            ("login", "status"): "signed in\n",
        }
        return subprocess.CompletedProcess(command, 0, outputs[args], "")

    result = ar.probe_engine(
        ar.DEFAULT_ENGINE_REGISTRY.descriptor("codex"),
        environ={
            "CODEX_BIN": str(binary),
            "CODEX_HOME": str(tmp_path / "codex-home"),
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
            "CODEX_HOME": str(tmp_path / "codex-home"),
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
            ("exec", "--help"): "--output-last-message --sandbox --cd\n",
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
            ("exec", "--help"): (0, "--output-last-message --sandbox --cd\n", ""),
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
            ("--version",): "Claude Code 2.1.0\n",
            ("--help",): "--output-format --permission-mode --allowedTools\n",
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
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in received_environments[1]
    assert received_environments[2]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-profile")
    assert received_environments[2]["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-auth-token"
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


def test_probe_reports_auth_required_without_leaking_output(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "codex")
    runner, _calls = _runner(
        {
            ("--version",): (0, "codex 1.2.3\n", ""),
            ("exec", "--help"): (0, "--output-last-message --sandbox --cd\n", ""),
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


def test_candidate_probe_never_claims_dispatch_readiness(fresh_agent_runner, tmp_path: Path):
    ar = fresh_agent_runner
    binary = _executable(tmp_path / "opencode")
    runner, _calls = _runner(
        {
            ("--version",): (0, "opencode 2.0.0\n", ""),
            ("run", "--help"): (0, "--format --model --dir --agent\n", ""),
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
    assert result.ready is False
    assert result.state == "needs_validation"
    assert result.failures == ("deep_probe_required",)


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
                "--output-last-message --sandbox --cd\n",
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

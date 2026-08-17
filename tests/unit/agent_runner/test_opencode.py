"""Contract tests for Alfred's OpenCode subprocess adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_command_is_headless_pure_and_scoped_to_the_worktree(fresh_agent_runner, tmp_path: Path):
    from agent_runner.opencode import build_opencode_command

    command = build_opencode_command(
        "/opt/opencode",
        workdir=tmp_path,
        model="anthropic/claude-sonnet-4-5",
    )

    assert command == [
        "/opt/opencode",
        "--pure",
        "run",
        "--format",
        "json",
        "--dir",
        str(tmp_path),
        "--agent",
        "alfred",
        "--model",
        "anthropic/claude-sonnet-4-5",
    ]
    assert "--auto" not in command
    assert "--dangerously-skip-permissions" not in command


def test_environment_isolates_config_and_denies_unexpected_boundaries(
    fresh_agent_runner, tmp_path: Path
):
    from agent_runner.opencode import opencode_environment

    config_dir = tmp_path / "config"
    environment = opencode_environment(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": "/usr/bin:/bin",
            "ANTHROPIC_API_KEY": "provider-secret",
            "OPENCODE_CONFIG": "/tmp/operator-opencode.json",
            "OPENCODE_CONFIG_CONTENT": '{"permission":"allow"}',
        },
        config_dir=config_dir,
        allow_writes=False,
    )

    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert environment["OPENCODE_CONFIG_DIR"] == str(config_dir)
    assert "OPENCODE_CONFIG" not in environment
    assert environment["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert environment["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "1"
    assert environment["OPENCODE_DISABLE_LSP_DOWNLOAD"] == "1"
    assert environment["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"
    assert environment["ANTHROPIC_API_KEY"] == "provider-secret"
    assert config["share"] == "disabled"
    assert config["permission"]["*"] == "allow"
    assert config["permission"]["external_directory"] == "deny"
    assert config["permission"]["question"] == "deny"
    assert config["permission"]["task"] == "deny"
    assert config["permission"]["skill"] == "deny"
    assert config["permission"]["mcp_*"] == "deny"
    assert config["permission"]["edit"] == "deny"
    assert config["permission"]["bash"] == "deny"
    assert config["agent"]["alfred"]["permission"] == config["permission"]


def test_write_environment_allows_worktree_tools_but_denies_release_actions(
    fresh_agent_runner, tmp_path: Path
):
    from agent_runner.opencode import opencode_environment

    environment = opencode_environment({}, config_dir=tmp_path, allow_writes=True)
    permissions = json.loads(environment["OPENCODE_CONFIG_CONTENT"])["permission"]

    assert permissions["edit"] == "allow"
    assert permissions["bash"]["*"] == "allow"
    assert permissions["bash"]["git push*"] == "deny"
    assert permissions["bash"]["gh pr merge*"] == "deny"
    assert permissions["external_directory"] == "deny"


def test_parser_returns_only_completed_text_and_usage(fresh_agent_runner):
    from agent_runner.opencode import parse_opencode_events

    payload = "\n".join(
        (
            json.dumps(
                {
                    "type": "step_start",
                    "sessionID": "ses_123",
                    "part": {"type": "step-start"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "sessionID": "ses_123",
                    "part": {
                        "type": "tool",
                        "tool": "read",
                        "state": {"status": "completed"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_123",
                    "part": {"type": "text", "text": "First finding."},
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_123",
                    "part": {"type": "text", "text": "Second finding."},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "ses_123",
                    "part": {
                        "type": "step-finish",
                        "cost": 0.125,
                        "tokens": {"input": 120, "output": 30, "reasoning": 5},
                    },
                }
            ),
        )
    )

    result = parse_opencode_events(payload)

    assert result.text == "First finding.\n\nSecond finding."
    assert result.session_id == "ses_123"
    assert result.tokens_used == 155
    assert result.cost_usd == 0.125
    assert result.tool_error is None
    assert result.error is None


def test_parser_fails_closed_on_malformed_or_permission_events(fresh_agent_runner):
    from agent_runner.opencode import parse_opencode_events

    malformed = parse_opencode_events('{"type":"text"}\nnot-json\n')
    denied = parse_opencode_events(
        json.dumps(
            {
                "type": "tool_use",
                "sessionID": "ses_denied",
                "part": {
                    "type": "tool",
                    "tool": "edit",
                    "state": {"status": "error", "error": "Permission denied"},
                },
            }
        )
    )

    assert malformed.parse_error == "OpenCode emitted malformed JSON events."
    assert denied.tool_error == "Permission denied"
    assert denied.session_id == "ses_denied"


def test_parser_extracts_structured_error_without_echoing_events(fresh_agent_runner):
    from agent_runner.opencode import parse_opencode_events

    result = parse_opencode_events(
        json.dumps(
            {
                "type": "error",
                "sessionID": "ses_error",
                "error": {
                    "name": "ProviderAuthError",
                    "data": {"message": "Authentication failed"},
                },
            }
        )
    )

    assert result.error == "Authentication failed"
    assert result.session_id == "ses_error"
    assert "ProviderAuthError" not in result.text


def _ready_probe(ar, binary: Path):
    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode")
    return ar.EngineProbeResult(
        descriptor=descriptor,
        installed=True,
        protocol_compatible=True,
        ready=True,
        state="ready",
        detail="ready",
        binary=str(binary),
        version="opencode 1.18.18",
    )


def test_invoke_uses_verified_binary_stdin_and_ephemeral_config(
    fresh_agent_runner, monkeypatch, tmp_path: Path
):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    binary = tmp_path / "verified" / "opencode"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(proc, "_probe_dispatch_engine", lambda _engine: _ready_probe(ar, binary))
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        assert Path(kwargs["env"]["OPENCODE_CONFIG_DIR"]).is_dir()
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_ok",
                    "part": {"type": "text", "text": "Done."},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(proc, "_popen_run_text", fake_run)

    result = ar.opencode_invoke(
        "Fix the selected issue.",
        workdir=tmp_path,
        agent="senior-dev",
        firing_id="open-1",
        model="anthropic/claude-sonnet-4-5",
        allow_writes=True,
    )

    assert result.success is True
    assert result.result_text == "Done."
    assert result.session_id == "ses_ok"
    assert captured["input_text"] == "Fix the selected issue."
    assert captured["cwd"] == str(tmp_path)
    assert captured["command"][0] == str(binary)
    assert "--auto" not in captured["command"]
    assert result.raw["engine"] == "opencode"
    assert "stdout" not in result.raw


def test_invoke_crosses_the_real_process_boundary(fresh_agent_runner, monkeypatch, tmp_path: Path):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    binary = tmp_path / "opencode"
    binary.write_text(
        """#!/bin/sh
[ "$1" = "--pure" ] || exit 31
[ -n "$OPENCODE_CONFIG_CONTENT" ] || exit 32
[ -d "$OPENCODE_CONFIG_DIR" ] || exit 33
IFS= read -r prompt
[ "$prompt" = "Real process prompt." ] || exit 34
printf '%s\n' \
  '{"type":"text","sessionID":"ses_real","part":{"type":"text","text":"Process complete."}}' \
  '{"type":"step_finish","sessionID":"ses_real","part":{"type":"step-finish","cost":0.02,"tokens":{"input":8,"output":3,"cache":{"read":2,"write":1}}}}'
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setattr(proc, "_probe_dispatch_engine", lambda _engine: _ready_probe(ar, binary))

    result = ar.opencode_invoke(
        "Real process prompt.",
        workdir=tmp_path,
        agent="reviewer",
        firing_id="open-real-process",
    )

    assert result.success is True
    assert result.result_text == "Process complete."
    assert result.session_id == "ses_real"
    assert result.cost_usd == 0.02
    assert result.raw["tokens_used"] == 14


def test_invoke_refuses_unready_engine(fresh_agent_runner, monkeypatch, tmp_path: Path):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    descriptor = ar.DEFAULT_ENGINE_REGISTRY.descriptor("opencode")
    monkeypatch.setattr(
        proc,
        "_probe_dispatch_engine",
        lambda _engine: ar.EngineProbeResult(
            descriptor=descriptor,
            installed=True,
            protocol_compatible=True,
            ready=False,
            state="auth_required",
            detail="signed out",
            binary="opencode",
            version="opencode 1.18.18",
            failures=("auth_required",),
        ),
    )
    monkeypatch.setattr(
        proc,
        "_popen_run_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unready OpenCode must not start")
        ),
    )

    result = ar.opencode_invoke("Read only", workdir=tmp_path, agent="reviewer")

    assert result.subtype == "error_authentication"
    assert result.raw == {"engine": "opencode", "engine_readiness": "auth_required"}


def test_invoke_maps_timeout_and_keeps_partial_artifacts(
    fresh_agent_runner, monkeypatch, tmp_path: Path
):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    monkeypatch.setattr(
        proc,
        "_probe_dispatch_engine",
        lambda _engine: _ready_probe(ar, tmp_path / "opencode"),
    )
    monkeypatch.setattr(
        proc,
        "_popen_run_text",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            124,
            stdout=json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_partial",
                    "part": {"type": "text", "text": "Partial"},
                }
            ),
            stderr="TIMEOUT after 9s",
        ),
    )

    result = ar.opencode_invoke(
        "Work",
        workdir=tmp_path,
        agent="senior-dev",
        firing_id="open-timeout",
        timeout=9,
    )

    assert result.subtype == "error_timeout"
    assert result.stop_reason == "aborted"
    assert result.session_id == "ses_partial"
    assert result.raw["timeout"] == 9
    assert Path(result.raw["stdout_path"]).read_text(encoding="utf-8").strip()


def test_invoke_maps_auth_quota_permission_parse_and_empty_failures(
    fresh_agent_runner, monkeypatch, tmp_path: Path
):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    monkeypatch.setattr(
        proc,
        "_probe_dispatch_engine",
        lambda _engine: _ready_probe(ar, tmp_path / "opencode"),
    )
    outputs = iter(
        (
            (
                1,
                json.dumps(
                    {"type": "error", "error": {"data": {"message": "Authentication failed"}}}
                ),
            ),
            (
                1,
                json.dumps(
                    {"type": "error", "error": {"data": {"message": "You've hit your usage limit"}}}
                ),
            ),
            (
                0,
                json.dumps(
                    {
                        "type": "tool_use",
                        "part": {
                            "type": "tool",
                            "tool": "edit",
                            "state": {"status": "error", "error": "Permission denied"},
                        },
                    }
                ),
            ),
            (0, "not-json"),
            (0, json.dumps({"type": "step_start", "part": {"type": "step-start"}})),
            (1, json.dumps({"type": "error", "error": {"name": "AbortError"}})),
        )
    )

    def fake_run(command, **_kwargs):
        returncode, stdout = next(outputs)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(proc, "_popen_run_text", fake_run)

    results = [
        ar.opencode_invoke("Work", workdir=tmp_path, agent="reviewer", firing_id=f"f-{idx}")
        for idx in range(6)
    ]

    assert [result.subtype for result in results] == [
        "error_authentication",
        "error_quota_exhausted",
        "error_permission",
        "parse-failed",
        "parse-failed",
        "error_cancelled",
    ]
    assert results[-1].stop_reason == "aborted"
    assert all(result.success is False for result in results)


def test_engine_router_dispatches_opencode_with_explicit_write_boundary(
    fresh_agent_runner, monkeypatch, tmp_path: Path
):
    ar = fresh_agent_runner
    captured: dict[str, object] = {}

    def fake_opencode(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return ar.ClaudeResult(
            success=True,
            subtype="success",
            num_turns=1,
            cost_usd=0.01,
            session_id="ses_route",
            result_text="routed",
            raw={},
            stop_reason="end_turn",
        )

    monkeypatch.setattr(ar, "load_runtime_memory", lambda: None)
    result, engine_used = ar.invoke_agent_engine(
        "Inspect the repo.",
        engine="opencode",
        agent="senior-dev",
        firing_id="route-open",
        workdir=tmp_path,
        claude_allowed_tools="Read,Write,Bash",
        timeout=30,
        opencode_model="openai/gpt-5",
        opencode_allow_writes=True,
        opencode_fn=fake_opencode,
    )

    assert result.success is True
    assert engine_used == "opencode"
    assert captured["model"] == "openai/gpt-5"
    assert captured["allow_writes"] is True
    assert captured["timeout"] == 30

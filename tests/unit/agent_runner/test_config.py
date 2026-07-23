"""Focused tests for ``lib.agent_runner.config``."""

from __future__ import annotations

import pytest


def test_env_int_clamps_to_range(fresh_agent_runner, monkeypatch):
    """env_int clamps both the env value and the fallback to the range."""
    ar = fresh_agent_runner
    monkeypatch.setenv("FOO", "1000")
    assert ar.env_int("FOO", default=5, minimum=1, maximum=10) == 10
    monkeypatch.setenv("FOO", "garbage")
    assert ar.env_int("FOO", default=7, minimum=1, maximum=10) == 7
    monkeypatch.delenv("FOO", raising=False)
    assert ar.env_int("FOO", default=99, minimum=1, maximum=10) == 10


def test_optional_env_int_returns_none_when_unset(fresh_agent_runner, monkeypatch):
    """optional_env_int returns None for missing/unparseable, otherwise clamped int."""
    ar = fresh_agent_runner
    monkeypatch.delenv("BAR", raising=False)
    assert ar.optional_env_int("BAR") is None
    monkeypatch.setenv("BAR", "not-an-int")
    assert ar.optional_env_int("BAR") is None
    monkeypatch.setenv("BAR", "5")
    assert ar.optional_env_int("BAR", minimum=10) == 10


def test_normalize_engine_accepts_only_current_names(fresh_agent_runner):
    """Unknown engine names fall back instead of carrying legacy aliases."""
    ar = fresh_agent_runner
    assert ar.normalize_engine("both", default="codex") == "codex"
    assert ar.normalize_engine("CODEX") == "codex"
    assert ar.normalize_engine("garbage", default="codex") == "codex"
    assert ar.normalize_engine(None) == "hybrid"


def test_agent_engine_env_precedence(fresh_agent_runner, monkeypatch):
    """ALFRED_<AGENT>_ENGINE wins over fleet-wide ALFRED_ENGINE."""
    ar = fresh_agent_runner
    monkeypatch.setenv("ALFRED_ENGINE", "codex")
    monkeypatch.setenv("ALFRED_SENIOR_DEV_ENGINE", "claude")
    assert ar.agent_engine("senior-dev") == "claude"


def test_agent_engine_ignores_removed_review_engine_alias(fresh_agent_runner, monkeypatch):
    """The reviewer uses the same canonical engine keys as every other agent."""
    ar = fresh_agent_runner
    monkeypatch.setenv("ALFRED_REVIEW_ENGINE", "codex")
    assert ar.agent_engine("reviewer", default="claude") == "claude"


def test_normalize_model_name_accepts_cli_aliases_and_rejects_unsafe_values(
    fresh_agent_runner,
):
    ar = fresh_agent_runner
    assert ar.normalize_model_name("claude-opus-4-8") == "claude-opus-4-8"
    assert ar.normalize_model_name("provider/model:v2") == "provider/model:v2"
    assert ar.normalize_model_name("--config") is None
    assert ar.normalize_model_name("model with spaces") is None
    assert ar.normalize_model_name("x" * 129) is None


def test_agent_model_precedence_and_state(fresh_agent_runner, monkeypatch):
    ar = fresh_agent_runner
    state_dir = ar.STATE_ROOT / "models" / "senior-dev"
    state_dir.mkdir(parents=True)
    (state_dir / "claude").write_text("state-sonnet\n", encoding="utf-8")
    (state_dir / "codex").write_text("state-codex\n", encoding="utf-8")

    assert ar.agent_model("senior-dev", "claude", environ={}) == "state-sonnet"
    assert ar.agent_model("senior-dev", "codex", environ={}) == "state-codex"
    assert (
        ar.agent_model(
            "senior-dev",
            "codex",
            environ={
                "ALFRED_CODEX_MODEL": "fleet-codex",
                "ALFRED_SENIOR_DEV_CODEX_MODEL": "agent-codex",
            },
        )
        == "agent-codex"
    )
    assert (
        ar.agent_model("senior-dev", "claude", environ={"ALFRED_CLAUDE_MODEL": "fleet"}) == "fleet"
    )

    monkeypatch.setenv("ALFRED_SENIOR_DEV_CLAUDE_MODEL", "not a model")
    assert ar.agent_model("senior-dev", "claude") is None


def test_agent_model_rejects_unknown_engine(fresh_agent_runner):
    with pytest.raises(ValueError, match="model engine"):
        fresh_agent_runner.agent_model("senior-dev", "hybrid")


def test_agent_model_ignores_removed_bare_codex_model_setting(fresh_agent_runner):
    assert (
        fresh_agent_runner.agent_model(
            "senior-dev",
            "codex",
            environ={"CODEX_MODEL": "legacy-codex"},
        )
        is None
    )


def test_engine_preflight_bins_modes(fresh_agent_runner):
    """codex needs codex; hybrid defaults to claude-only; opt-in adds codex."""
    ar = fresh_agent_runner
    assert ar.engine_preflight_bins("codex") == [ar.CODEX_BIN]
    assert ar.engine_preflight_bins("hybrid") == [ar.CLAUDE_BIN]
    assert ar.engine_preflight_bins("hybrid", hybrid_requires_codex=True) == [
        ar.CLAUDE_BIN,
        ar.CODEX_BIN,
    ]


def test_doctor_mode_truthy_env(fresh_agent_runner, monkeypatch):
    """doctor_mode honours common truthy strings."""
    ar = fresh_agent_runner
    monkeypatch.delenv("ALFRED_DOCTOR", raising=False)
    assert not ar.doctor_mode()
    monkeypatch.setenv("ALFRED_DOCTOR", "1")
    assert ar.doctor_mode()
    monkeypatch.setenv("ALFRED_DOCTOR", "0")
    assert not ar.doctor_mode()


def test_dry_run_toggle(fresh_agent_runner, monkeypatch):
    """set_dry_run writes the env var; is_dry_run picks it up."""
    ar = fresh_agent_runner
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    assert not ar.is_dry_run()
    ar.set_dry_run(True)
    assert ar.is_dry_run()
    ar.set_dry_run(False)
    assert not ar.is_dry_run()


def test_codex_sandbox_for_agent_precedence(fresh_agent_runner, monkeypatch):
    """ALFRED_<AGENT>_CODEX_SANDBOX > CODEX_WRITE > default."""
    ar = fresh_agent_runner
    monkeypatch.setenv("ALFRED_SENIOR_DEV_CODEX_WRITE", "1")
    assert ar.codex_sandbox_for_agent("senior-dev") == "workspace-write"
    monkeypatch.setenv("LUCIUS" + "_CODEX_SANDBOX", "danger-full-access")
    assert ar.codex_sandbox_for_agent("senior-dev") == "workspace-write"
    monkeypatch.setenv("ALFRED_SENIOR_DEV_CODEX_SANDBOX", "read-only")
    assert ar.codex_sandbox_for_agent("senior-dev") == "read-only"

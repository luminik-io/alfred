"""Tests for canonical env-flag parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from envflags import TRUTHY_VALUES, truthy  # noqa: E402


def test_truthy_accepts_only_documented_tokens() -> None:
    """The shared env flag parser has one documented true vocabulary."""
    assert frozenset({"1", "true", "yes", "on", "enabled"}) == TRUTHY_VALUES

    for value in ("1", "true", "TRUE", " yes ", "on", "enabled", True):
        assert truthy(value) is True

    for value in (None, "", "0", "false", "no", "off", "disabled", "maybe", False):
        assert truthy(value) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled"])
def test_agent_runner_truthy_env_uses_shared_tokens(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """agent_runner.config keeps env-name convenience on top of truthy()."""
    import agent_runner.config as config

    monkeypatch.setenv("ALFRED_TEST_FLAG", value)
    assert config._truthy_env("ALFRED_TEST_FLAG") is True


def test_legacy_env_wrappers_use_shared_truthy_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Representative legacy wrappers accept the canonical true vocabulary."""
    import memory_extract
    from agent_runner import process

    assert memory_extract.extract_enabled({"ALFRED_MEMORY_EXTRACT": "enabled"}) is True

    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "enabled")
    assert process._graphify_mcp_enabled() is True


def test_default_on_env_wrappers_use_shared_false_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-on wrappers share the canonical false vocabulary."""
    from agent_runner import process

    monkeypatch.setenv("ALFRED_MEMORY_MCP", "disabled")
    assert process._memory_mcp_enabled() is False


def test_disabled_judge_selects_conservative_no_judge_promotion_path() -> None:
    import memory_judge
    from fleet_brain import config

    env = {"ALFRED_AUTO_PROMOTE_LLM_JUDGE": "disabled"}
    assert memory_judge.judge_enabled(env) is False
    assert config._llm_judge_flag_allows_auto_promote(env) is True


def test_disabled_kill_token_means_the_kill_switch_is_inactive() -> None:
    from agent_runner import paths

    assert paths._runtime_stop_control_active("ALFRED_AUTO_PROMOTE_KILL", "disabled") is False

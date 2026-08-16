"""Focused tests for fleet-brain environment policy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from fleet_brain.config import (  # noqa: E402
    auto_promote_behavior_changes_enabled,
    direct_auto_promote_env,
)


@pytest.mark.parametrize("token", ["1", "true", "YES", "on", "enabled", "1 # explicit opt-in"])
def test_behavior_change_auto_promotion_arms_only_on_explicit_truthy_token(token: str) -> None:
    assert auto_promote_behavior_changes_enabled({"ALFRED_AUTO_PROMOTE_BEHAVIOR_CHANGES": token})


@pytest.mark.parametrize(
    "token", [None, "", "0", "false", "no", "off", "disabled", "treu", "treu # typo"]
)
def test_behavior_change_auto_promotion_defaults_off_and_fails_closed(
    token: str | None,
) -> None:
    env = {} if token is None else {"ALFRED_AUTO_PROMOTE_BEHAVIOR_CHANGES": token}
    assert not auto_promote_behavior_changes_enabled(env)


def test_direct_auto_promote_env_prefers_runtime_behavior_change_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text("ALFRED_AUTO_PROMOTE_BEHAVIOR_CHANGES=0\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setenv("ALFRED_AUTO_PROMOTE_BEHAVIOR_CHANGES", "1")

    env = direct_auto_promote_env()

    assert env["ALFRED_AUTO_PROMOTE_BEHAVIOR_CHANGES"] == "0"

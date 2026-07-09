#!/usr/bin/env python3
"""Tests for lib/compression_engine.py - the engine selector.

Covers:
  1. Engine selection (builtin default, off, headroom) from config.
  2. No-op fallback to builtin when headroom is unavailable.
  3. The #453 safety valve is preserved THROUGH the headroom path: an errored
     or unknown-status output is never handed to headroom (mocked here).
  4. Default behaviour is byte-identical to the built-in compactor.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import compression_engine as ce  # noqa: E402
import pytest  # noqa: E402
import tool_compactor as tc  # noqa: E402


def _big_log(n: int = 600) -> str:
    return "\n".join(f"building module {i} ... done" for i in range(n)) + "\n"


class _Recorder:
    """A stand-in headroom that records what it was asked to compress."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def available(self, env=None) -> bool:
        return True

    def compress(self, text, *, env=None, **kwargs):
        self.calls.append(text)
        # A deterministic "compression": keep only the first line.
        return text.split("\n", 1)[0]


def _install_headroom(monkeypatch: pytest.MonkeyPatch, rec: _Recorder) -> None:
    monkeypatch.setattr(ce.headroom_engine, "headroom_available", rec.available)
    monkeypatch.setattr(ce.headroom_engine, "compress", rec.compress)


# --------------------------------------------------------------------------
# Engine selection
# --------------------------------------------------------------------------
def test_default_engine_is_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_COMPRESSION_ENGINE", raising=False)
    assert ce.selected_engine({}) == "builtin"


def test_unknown_engine_falls_back_to_builtin() -> None:
    assert ce.selected_engine({"ALFRED_COMPRESSION_ENGINE": "bogus"}) == "builtin"


def test_engine_off_disables_compaction() -> None:
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "off"}
    )
    assert not result.applied
    assert result.reason == "engine_off"
    assert result.text == raw


# --------------------------------------------------------------------------
# builtin parity: default path is byte-identical to tool_compactor
# --------------------------------------------------------------------------
def test_builtin_path_matches_tool_compactor() -> None:
    raw = _big_log()
    env = {"ALFRED_COMPRESSION_ENGINE": "builtin"}
    via = ce.compact_output_via_engine(raw, tool_name="Bash", exit_code=0, env=env)
    direct = tc.compact_output(raw, tool_name="Bash", exit_code=0, env=env)
    assert via.applied and direct.applied
    assert via.text == direct.text
    assert via.reason == direct.reason == "compacted"


def test_unset_engine_matches_builtin() -> None:
    raw = _big_log()
    via = ce.compact_output_via_engine(raw, tool_name="Bash", exit_code=0, env={})
    direct = tc.compact_output(raw, tool_name="Bash", exit_code=0, env={})
    assert via.text == direct.text


# --------------------------------------------------------------------------
# headroom routing + no-op fallback
# --------------------------------------------------------------------------
def test_headroom_used_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert result.applied
    assert result.reason == "compacted_headroom"
    assert rec.calls == [raw]  # headroom saw the confirmed-success output
    assert result.text == "building module 0 ... done"
    assert result.final_bytes < result.original_bytes


def test_headroom_unavailable_falls_back_to_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce.headroom_engine, "headroom_available", lambda env=None: False)
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    # Fell back to the built-in compactor: still compacted, but not the headroom
    # reason.
    assert result.applied
    assert result.reason == "compacted"


def test_hook_path_never_autofetches(monkeypatch: pytest.MonkeyPatch) -> None:
    # CRITICAL (Greptile P1): the PostToolUse hook path must NEVER shell out to an
    # installer inline - that would hang the agent's tool call. When headroom is
    # absent the selector falls straight back to builtin and touches no installer.
    monkeypatch.setattr(ce.headroom_engine, "headroom_available", lambda env=None: False)
    installer_calls: list[object] = []
    monkeypatch.setattr(
        ce.headroom_engine,
        "maybe_autofetch",
        lambda env=None: installer_calls.append(True),
    )
    # Belt and suspenders: any real subprocess would also be a violation.
    monkeypatch.setattr(
        ce.headroom_engine.subprocess,
        "run",
        lambda *a, **k: installer_calls.append(("subprocess", a)),
    )
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert result.applied and result.reason == "compacted"  # builtin fallback
    assert installer_calls == []  # installer NEVER invoked on the hook path


def test_headroom_declines_falls_back_to_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce.headroom_engine, "headroom_available", lambda env=None: True)
    monkeypatch.setattr(ce.headroom_engine, "compress", lambda text, **k: None)
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert result.applied
    assert result.reason == "compacted"  # built-in fallback still shrank it


def test_headroom_no_gain_falls_back_to_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    # headroom returns something no smaller than the input -> built-in fallback.
    monkeypatch.setattr(ce.headroom_engine, "headroom_available", lambda env=None: True)
    monkeypatch.setattr(ce.headroom_engine, "compress", lambda text, **k: text + " and more")
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert result.reason == "compacted"


# --------------------------------------------------------------------------
# CRITICAL: the safety valve is preserved through the headroom path
# --------------------------------------------------------------------------
def test_headroom_never_sees_failed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = _big_log() + "make: *** [build] Error 1\n"
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=1, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert not result.applied
    assert result.reason == "teed_on_failure"
    assert result.text == raw  # full output preserved
    assert rec.calls == []  # headroom was NEVER handed the errored output


def test_headroom_never_sees_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = _big_log() + "make: *** No rule to make target 'all'.  Stop.\n"
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=None, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert not result.applied
    assert result.reason == "unknown_status"
    assert result.text == raw
    assert rec.calls == []


def test_headroom_respects_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw,
        tool_name="Bash",
        exit_code=0,
        env={"ALFRED_COMPRESSION_ENGINE": "headroom", "ALFRED_OUTPUT_COMPACTOR": "0"},
    )
    assert not result.applied
    assert result.reason == "disabled"
    assert rec.calls == []


def test_headroom_respects_tool_targeting(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = _big_log()
    result = ce.compact_output_via_engine(
        raw, tool_name="Read", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert not result.applied
    assert result.reason == "tool_not_targeted"
    assert rec.calls == []


def test_headroom_respects_min_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    _install_headroom(monkeypatch, rec)
    raw = "small output\n"
    result = ce.compact_output_via_engine(
        raw, tool_name="Bash", exit_code=0, env={"ALFRED_COMPRESSION_ENGINE": "headroom"}
    )
    assert not result.applied
    assert result.reason == "within_budget"
    assert rec.calls == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

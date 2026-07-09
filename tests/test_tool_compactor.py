#!/usr/bin/env python3
"""Tests for lib/tool_compactor.py - deterministic tool-output compaction.

Two concerns are covered explicitly:
  1. Deterministic compaction of noisy, low-signal Bash output.
  2. The CRITICAL confirmed-success safety valve: output is compacted ONLY on a
     structured exit code of 0. A non-zero exit or an unknown status (no exit
     code) passes through untouched, so an error can never be hidden.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import pytest  # noqa: E402
import tool_compactor as tc  # noqa: E402


# --------------------------------------------------------------------------
# Cleaning primitives
# --------------------------------------------------------------------------
def test_strip_ansi_removes_colour_codes() -> None:
    coloured = "\x1b[31mred\x1b[0m plain \x1b[1;32mgreen\x1b[0m"
    assert tc.strip_ansi(coloured) == "red plain green"


def test_strip_ansi_empty_is_safe() -> None:
    assert tc.strip_ansi("") == ""


# --------------------------------------------------------------------------
# Compaction
# --------------------------------------------------------------------------
def _big_generic_log(n: int = 600) -> str:
    return "\n".join(f"building module {i} ... done" for i in range(n)) + "\n"


def _all_pass_test_log(n: int = 500) -> str:
    passed = "\n".join(f"PASSED tests/test_mod.py::test_{i}" for i in range(n))
    return f"{passed}\n===== {n} passed in 12.34s =====\n"


def test_compact_generic_log_head_tail_with_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR", raising=False)
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.applied
    assert result.reason == "compacted"
    assert result.final_bytes < result.original_bytes
    assert "ALFRED_OUTPUT_COMPACTOR omitted_lines=" in result.text
    assert result.omitted_lines > 0
    # Head and tail survive.
    assert "building module 0 ... done" in result.text
    assert "building module 599 ... done" in result.text


def test_compact_respects_byte_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_MAX_BYTES", "1500")
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_HEAD_LINES", "60")
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_TAIL_LINES", "60")
    raw = _big_generic_log(1000)
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.applied
    # Budget is honored (small marker slack tolerated by the trimming loop).
    assert result.final_bytes <= 1500


def test_compact_all_pass_test_log_summarized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR", raising=False)
    raw = _all_pass_test_log()
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.applied
    assert "all passed" in result.text
    assert "500 passed" in result.text
    assert result.final_bytes < result.original_bytes


def test_compact_strips_ansi_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_MIN_BYTES", "50")
    raw = "\x1b[33mwarn: retry\x1b[0m\n" * 400
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.applied
    assert "\x1b[" not in result.text  # ANSI gone
    assert "(x" in result.text  # consecutive duplicates collapsed


# --------------------------------------------------------------------------
# Safety valve: compact ONLY on confirmed success (never hide an error)
# --------------------------------------------------------------------------
def test_confirmed_success_is_compacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR", raising=False)
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.applied
    assert result.reason == "compacted"
    assert result.final_bytes < result.original_bytes


def test_tee_on_nonzero_exit_preserves_full_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR", raising=False)
    raw = _big_generic_log() + "make: *** [build] Error 1\n"
    result = tc.compact_output(raw, tool_name="Bash", exit_code=1)
    assert not result.applied
    assert result.reason == "teed_on_failure"
    assert result.text == raw  # byte-for-byte, nothing hidden


def test_unknown_status_passes_through_make_error() -> None:
    # A plain-string response carries no exit code. `make: *** No rule...` is an
    # error format the compactor was never taught to recognize - and with the
    # inverted valve it does not need to be. Unknown status => never compact.
    raw = _big_generic_log() + "make: *** No rule to make target 'all'.  Stop.\n"
    result = tc.compact_output(raw, tool_name="Bash", exit_code=None)
    assert not result.applied
    assert result.reason == "unknown_status"
    assert result.text == raw  # full output preserved, error not hidden


def test_unknown_status_passes_through_even_clean_success_looking_output() -> None:
    # Even output that looks perfectly successful is NOT compacted without an
    # exit code: compaction requires positive proof, not the absence of errors.
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Bash", exit_code=None)
    assert not result.applied
    assert result.reason == "unknown_status"
    assert result.text == raw


# --------------------------------------------------------------------------
# Config gates
# --------------------------------------------------------------------------
def test_disabled_flag_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR", "0")
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert not result.applied
    assert result.reason == "disabled"
    assert result.text == raw


def test_untargeted_tool_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR_TOOLS", raising=False)
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Read", exit_code=0)
    assert not result.applied
    assert result.reason == "tool_not_targeted"


def test_tool_targeting_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_TOOLS", "Bash,Read")
    raw = _big_generic_log()
    result = tc.compact_output(raw, tool_name="Read", exit_code=0)
    assert result.applied


def test_small_output_within_budget_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR_MIN_BYTES", raising=False)
    raw = "done\n"
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert not result.applied
    assert result.reason == "within_budget"
    assert result.text == raw


def test_bad_int_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_OUTPUT_COMPACTOR_MIN_BYTES", "not-a-number")
    raw = "x\n"  # tiny, so default 2000 floor keeps it un-compacted
    result = tc.compact_output(raw, tool_name="Bash", exit_code=0)
    assert result.reason == "within_budget"


def test_empty_output_is_safe() -> None:
    result = tc.compact_output("", tool_name="Bash", exit_code=0)
    assert not result.applied
    assert result.text == ""


# --------------------------------------------------------------------------
# Hook-path invariants: stdlib-only + wired into the settings payload
# --------------------------------------------------------------------------
def test_compactor_and_hook_are_stdlib_only() -> None:
    """The hook path must import nothing outside the standard library."""
    import ast
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", set()))
    # The hook path is these two sibling modules only.
    local = {"tool_compactor", "alfred_hooks"}
    for name in ("tool_compactor.py", "alfred_hooks.py"):
        source = (_LIB / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                assert root in stdlib or root in local, f"{name} imports non-stdlib {root!r}"


def test_posttooluse_hook_wired_into_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_AGENT_HOOKS", "1")
    from agent_runner import process

    settings = process._agent_hook_settings()
    hooks = settings["hooks"]
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    post_cmd = hooks["PostToolUse"][0]["hooks"][0]["command"]
    assert "posttooluse" in post_cmd and "alfred_hooks.py" in post_cmd


def test_hook_settings_empty_when_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_AGENT_HOOKS", "0")
    from agent_runner import process

    assert process._agent_hook_settings() == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

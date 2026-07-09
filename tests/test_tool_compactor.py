#!/usr/bin/env python3
"""Tests for lib/tool_compactor.py - output compaction + command normalization.

Three concerns are covered explicitly:
  1. Deterministic compaction of noisy, low-signal Bash output.
  2. The allowlisted PreToolUse command normalizer.
  3. The CRITICAL tee-on-failure safety valve: a failed command's full output is
     never compacted, so an error can never be hidden from the model.
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
# Safety valve: tee full output on failure (never hide an error)
# --------------------------------------------------------------------------
def test_tee_on_nonzero_exit_preserves_full_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_OUTPUT_COMPACTOR", raising=False)
    raw = _big_generic_log() + "make: *** [build] Error 1\n"
    result = tc.compact_output(raw, tool_name="Bash", exit_code=1)
    assert not result.applied
    assert result.reason == "teed_on_failure"
    assert result.text == raw  # byte-for-byte, nothing hidden


def test_tee_on_traceback_signature_without_exit_code() -> None:
    raw = (
        _big_generic_log()
        + "Traceback (most recent call last):\n"
        + '  File "app.py", line 10, in <module>\n'
        + "ValueError: boom\n"
    )
    result = tc.compact_output(raw, tool_name="Bash", exit_code=None)
    assert not result.applied
    assert result.reason == "teed_on_failure"
    assert "Traceback" in result.text
    assert result.text == raw


def test_tee_on_failing_test_tail_even_without_exit_code() -> None:
    passed = "\n".join(f"PASSED tests/test_mod.py::test_{i}" for i in range(400))
    raw = (
        f"{passed}\n"
        "FAILED tests/test_mod.py::test_boom - AssertionError: nope\n"
        "===== 1 failed, 400 passed in 12.3s =====\n"
    )
    result = tc.compact_output(raw, tool_name="Bash", exit_code=None)
    assert not result.applied
    assert result.reason == "teed_on_failure"
    assert "test_boom" in result.text  # the failing node survives in full
    assert result.text == raw


def test_looks_like_failure_matrix() -> None:
    assert tc.looks_like_failure("all good", exit_code=2) is True
    assert tc.looks_like_failure("fatal: not a git repository", exit_code=None) is True
    assert tc.looks_like_failure("npm ERR! missing script", exit_code=None) is True
    # An all-green log with the word "error" incidentally must NOT trip it.
    assert tc.looks_like_failure("compiled with 0 errors", exit_code=0) is False
    assert tc.looks_like_failure("everything is fine", exit_code=None) is False


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
# PreToolUse command normalizer (allowlist only)
# --------------------------------------------------------------------------
def test_normalize_git_status() -> None:
    new, changed, note = tc.normalize_command("git status")
    assert changed
    assert new == "git status --short --branch"
    assert "porcelain" in note


def test_normalize_git_pull_fetch_clone() -> None:
    for verb in ("pull", "fetch"):
        new, changed, _ = tc.normalize_command(f"git {verb}")
        assert changed
        assert "--quiet" in new
    new, changed, _ = tc.normalize_command("git clone https://example.com/x.git")
    assert changed
    assert new == "git clone --quiet https://example.com/x.git"


def test_normalize_preserves_extra_args() -> None:
    new, changed, _ = tc.normalize_command("git pull origin main")
    assert changed
    assert new == "git pull --quiet origin main"


def test_normalize_skips_already_quiet() -> None:
    new, changed, _ = tc.normalize_command("git pull --quiet")
    assert not changed
    assert new == "git pull --quiet"


def test_normalize_skips_verbose_intent() -> None:
    # An explicit --verbose means the agent wants the noise; do not fight it.
    _new, changed, _ = tc.normalize_command("git fetch --verbose")
    assert not changed


def test_normalize_refuses_compound_commands() -> None:
    for cmd in ("git status | grep foo", "git status && echo done", "git status > out.txt"):
        new, changed, note = tc.normalize_command(cmd)
        assert not changed, cmd
        assert note == "compound"
        assert new == cmd


def test_normalize_unknown_command_unchanged() -> None:
    new, changed, note = tc.normalize_command("ls -la")
    assert not changed
    assert note == "no_rule"
    assert new == "ls -la"


def test_normalize_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_CMD_NORMALIZER", "0")
    new, changed, note = tc.normalize_command("git status")
    assert not changed
    assert note == "disabled"
    assert new == "git status"


def test_normalize_empty_command() -> None:
    _new, changed, note = tc.normalize_command("   ")
    assert not changed
    assert note == "empty"


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

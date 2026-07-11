#!/usr/bin/env python3
"""Tests for lib/tool_offload.py - offload oversized tool output to a file.

Covers the offload roundtrip (full output re-readable by path), the preview +
pointer format, the per-firing disk bound, the enable/disable flag, index
increment, firing-id sanitisation, and the cleanup sweep.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tool_offload as to  # noqa: E402


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {"ALFRED_HOME": str(tmp_path)}
    env.update(extra)
    return env


def _big(n: int = 300) -> str:
    return "\n".join(f"line {i}" for i in range(n))


# --------------------------------------------------------------------------
# Roundtrip + path
# --------------------------------------------------------------------------
def test_offload_roundtrip_saves_full_output(tmp_path: Path) -> None:
    full = _big()
    result = to.offload(full, firing_id="fire-1", env=_env(tmp_path))
    assert result.applied
    assert result.path is not None
    saved = Path(result.path).read_text(encoding="utf-8")
    assert saved == full  # the FULL output is recoverable byte-for-byte


def test_offload_path_is_firing_scoped(tmp_path: Path) -> None:
    result = to.offload(_big(), firing_id="fire-abc", env=_env(tmp_path))
    path = Path(result.path or "")
    assert path.parent == tmp_path / "state" / "firings" / "fire-abc" / "tool-output"
    assert path.name == "1.txt"


def test_offload_index_increments_within_firing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    r1 = to.offload(_big(), firing_id="fire-1", env=env)
    r2 = to.offload(_big(), firing_id="fire-1", env=env)
    assert (r1.index, r2.index) == (1, 2)
    assert Path(r2.path or "").name == "2.txt"


def test_firing_id_is_sanitised(tmp_path: Path) -> None:
    result = to.offload(_big(), firing_id="../../etc/passwd", env=_env(tmp_path))
    path = Path(result.path or "")
    # No traversal escapes the firings root.
    firings_root = (tmp_path / "state" / "firings").resolve()
    assert str(path).startswith(str(firings_root))


def test_missing_firing_id_uses_unknown_bucket(tmp_path: Path) -> None:
    result = to.offload(_big(), firing_id=None, env=_env(tmp_path))
    assert result.applied
    assert "unknown" in (result.path or "")


# --------------------------------------------------------------------------
# Preview / pointer format
# --------------------------------------------------------------------------
def test_preview_contains_pointer_and_head_and_tail(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        ALFRED_TOOL_OFFLOAD_PREVIEW_HEAD_LINES="5",
        ALFRED_TOOL_OFFLOAD_PREVIEW_TAIL_LINES="5",
    )
    result = to.offload(_big(300), firing_id="fire-1", env=env)
    text = result.text
    assert "line 0" in text  # head
    assert "line 299" in text  # tail
    assert "line 150" not in text  # middle omitted
    assert f"Full output saved to {result.path}" in text
    assert "[ALFRED_TOOL_OFFLOAD" in text and "[/ALFRED_TOOL_OFFLOAD]" in text
    assert "omitted_lines=290" in text


def test_short_output_preview_omits_nothing_but_still_points(tmp_path: Path) -> None:
    result = to.offload("a\nb\nc", firing_id="fire-1", env=_env(tmp_path))
    assert "omitted_lines=0" in result.text
    assert "Full output saved to" in result.text


# --------------------------------------------------------------------------
# Disk bound
# --------------------------------------------------------------------------
def test_disk_bound_blocks_offload(tmp_path: Path) -> None:
    env = _env(tmp_path, ALFRED_TOOL_OFFLOAD_MAX_BYTES="10")
    result = to.offload(_big(), firing_id="fire-1", env=env)
    assert not result.applied
    assert result.reason == "disk_bound_exceeded"


def test_disk_bound_is_per_firing_cumulative(tmp_path: Path) -> None:
    # Budget fits one small write but not two.
    payload = "x" * 40
    env = _env(tmp_path, ALFRED_TOOL_OFFLOAD_MAX_BYTES="60")
    first = to.offload(payload, firing_id="fire-1", env=env)
    second = to.offload(payload, firing_id="fire-1", env=env)
    assert first.applied
    assert not second.applied and second.reason == "disk_bound_exceeded"


# --------------------------------------------------------------------------
# Enable / disable
# --------------------------------------------------------------------------
def test_disabled_flag_skips_offload(tmp_path: Path) -> None:
    result = to.offload(_big(), firing_id="fire-1", env=_env(tmp_path, ALFRED_TOOL_OFFLOAD="0"))
    assert not result.applied
    assert result.reason == "disabled"
    assert result.text == _big()  # returns the input unchanged


def test_offload_enabled_default_on() -> None:
    assert to.offload_enabled({}) is True
    assert to.offload_enabled({"ALFRED_TOOL_OFFLOAD": "off"}) is False


# --------------------------------------------------------------------------
# Cleanup sweep
# --------------------------------------------------------------------------
def test_sweep_removes_aged_firing_dirs_and_keeps_fresh(tmp_path: Path) -> None:
    env = _env(tmp_path)
    to.offload(_big(), firing_id="old", env=env)
    to.offload(_big(), firing_id="fresh", env=env)

    old_dir = to.firing_offload_dir("old", env).parent
    ten_days_ago = time.time() - 10 * 86400
    import os

    os.utime(old_dir, (ten_days_ago, ten_days_ago))

    removed, mb_freed = to.sweep_expired(5 * 86400, now=time.time(), env=env)
    assert removed == 1
    assert mb_freed >= 0.0
    assert not old_dir.exists()
    assert to.firing_offload_dir("fresh", env).parent.exists()


def test_sweep_missing_root_is_noop(tmp_path: Path) -> None:
    removed, mb = to.sweep_expired(1.0, now=time.time(), env=_env(tmp_path))
    assert removed == 0
    assert mb == 0.0

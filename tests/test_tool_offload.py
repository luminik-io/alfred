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


def test_dot_firing_ids_cannot_escape_firings_root(tmp_path: Path) -> None:
    firings_root = (tmp_path / "state" / "firings").resolve()
    for firing_id in (".", ".."):
        result = to.offload(_big(), firing_id=firing_id, env=_env(tmp_path))
        path = Path(result.path or "").resolve()
        assert firings_root in path.parents
        assert path.parent == firings_root / "unknown" / "tool-output"


def test_missing_firing_id_uses_unknown_bucket(tmp_path: Path) -> None:
    result = to.offload(_big(), firing_id=None, env=_env(tmp_path))
    assert result.applied
    assert "unknown" in (result.path or "")


def test_unknown_bucket_symlink_cannot_escape_firings_root(tmp_path: Path) -> None:
    firings_root = tmp_path / "state" / "firings"
    outside = tmp_path / "outside"
    firings_root.mkdir(parents=True)
    outside.mkdir()
    (firings_root / "unknown").symlink_to(outside, target_is_directory=True)

    result = to.offload(_big(), firing_id="..", env=_env(tmp_path))

    assert result.applied is False
    assert result.reason == "unsafe_path"
    assert result.path is None
    assert not (outside / "tool-output").exists()


def test_firing_symlink_escape_is_rejected_instead_of_rebucketed(tmp_path: Path) -> None:
    firings_root = tmp_path / "state" / "firings"
    outside = tmp_path / "outside"
    firings_root.mkdir(parents=True)
    outside.mkdir()
    (firings_root / "fire-1").symlink_to(outside, target_is_directory=True)

    result = to.offload(_big(), firing_id="fire-1", env=_env(tmp_path))

    assert result.applied is False
    assert result.reason == "unsafe_path"
    assert result.path is None
    assert not (firings_root / "unknown").exists()
    assert not (outside / "tool-output").exists()


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

    old_offload = to.firing_offload_dir("old", env)
    old_firing = old_offload.parent
    ten_days_ago = time.time() - 10 * 86400
    import os

    os.utime(old_offload, (ten_days_ago, ten_days_ago))

    removed, mb_freed = to.sweep_expired(5 * 86400, now=time.time(), env=env)
    assert removed == 1
    assert mb_freed >= 0.0
    # The offload subtree is gone AND the now-empty firing dir is reaped.
    assert not old_offload.exists()
    assert not old_firing.exists()
    assert to.firing_offload_dir("fresh", env).parent.exists()


def test_sweep_preserves_sibling_firing_state(tmp_path: Path) -> None:
    # Offload only owns tool-output/: any sibling firing-scoped state another
    # feature stores under state/firings/<id>/ must survive the sweep.
    env = _env(tmp_path)
    to.offload(_big(), firing_id="fire-1", env=env)
    offload_dir = to.firing_offload_dir("fire-1", env)
    firing_dir = offload_dir.parent
    sibling = firing_dir / "metadata.json"
    sibling.write_text("{}", encoding="utf-8")

    ten_days_ago = time.time() - 10 * 86400
    import os

    os.utime(offload_dir, (ten_days_ago, ten_days_ago))

    removed, _ = to.sweep_expired(5 * 86400, now=time.time(), env=env)
    assert removed == 1
    assert not offload_dir.exists()
    assert sibling.exists()  # sibling state untouched
    assert firing_dir.exists()  # non-empty firing dir is NOT reaped


def test_sweep_missing_root_is_noop(tmp_path: Path) -> None:
    removed, mb = to.sweep_expired(1.0, now=time.time(), env=_env(tmp_path))
    assert removed == 0
    assert mb == 0.0


# --------------------------------------------------------------------------
# Concurrency + preview byte cap
# --------------------------------------------------------------------------
def test_offload_never_overwrites_a_concurrently_claimed_index(tmp_path: Path) -> None:
    # Simulate the loser of a claim race: another hook process already created
    # 1.txt after our _next_index scan would have returned 1. The O_EXCL claim
    # loop must advance to 2.txt and leave the first output intact.
    env = _env(tmp_path)
    directory = to.firing_offload_dir("fire-1", env)
    directory.mkdir(parents=True)
    (directory / "1.txt").write_text("first process output", encoding="utf-8")

    result = to.offload(_big(), firing_id="fire-1", env=env)
    assert result.applied
    assert result.index == 2
    assert (directory / "1.txt").read_text(encoding="utf-8") == "first process output"


def test_preview_respects_compaction_byte_budget_on_long_lines(tmp_path: Path) -> None:
    # A few multi-hundred-KB lines defeat a line-count-only preview. The preview
    # must honour the compaction byte budget it replaces.
    env = _env(tmp_path, ALFRED_OUTPUT_COMPACTOR_MAX_BYTES="4000")
    full = "\n".join("x" * 200_000 for _ in range(5))
    result = to.offload(full, firing_id="fire-1", env=env)
    assert result.applied
    assert len(result.text.encode("utf-8")) <= 4000
    assert "Full output saved to" in result.text
    # The saved file still holds the full output.
    assert Path(result.path or "").read_text(encoding="utf-8") == full


def test_preview_byte_cap_defaults_to_model_derived_budget(tmp_path: Path) -> None:
    # No explicit compactor override: the cap is the model-derived max (8000 for
    # the conservative 200K default window).
    env = _env(tmp_path)
    full = "\n".join("y" * 100_000 for _ in range(4))
    result = to.offload(full, firing_id="fire-1", env=env)
    assert result.applied
    assert len(result.text.encode("utf-8")) <= 8000

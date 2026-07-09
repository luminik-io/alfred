"""Tests for the per-worktree delta read ledger."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from agent_runner.read_ledger import (  # noqa: E402
    ReadLedger,
    delta_max_ratio,
    ledger_root_for,
    read_delta_available,
    read_delta_enabled,
)


def test_first_read_is_full_and_recorded(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    result = ledger.surface("repo:a.py", "line one\nline two\n")

    assert result.mode == "full"
    assert result.reason == "first_read"
    assert result.content == "line one\nline two\n"
    assert ledger.get("repo:a.py") == "line one\nline two\n"


def test_identical_reread_is_unchanged(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    content = "alpha\nbeta\ngamma\n"
    ledger.surface("repo:a.py", content)
    again = ledger.surface("repo:a.py", content)

    assert again.mode == "unchanged"
    assert again.content == ""
    assert again.reason == "identical_to_prior_read"


def test_small_change_returns_delta(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    original = "\n".join(f"line {i}" for i in range(1, 40)) + "\n"
    changed = original.replace("line 20", "line 20 CHANGED")
    ledger.surface("repo:a.py", original)
    delta = ledger.surface("repo:a.py", changed)

    assert delta.mode == "delta"
    assert delta.reason == "changed_since_prior_read"
    assert delta.content == ""
    assert "line 20 CHANGED" in delta.diff
    assert delta.diff.startswith("--- a/repo:a.py")
    # The ledger now holds the newest content for the next re-read.
    assert ledger.get("repo:a.py") == changed


def test_large_change_falls_back_to_full(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    ledger.surface("repo:a.py", "one\ntwo\nthree\n")
    # Wholesale replacement: the diff is bigger than the ratio allows.
    replacement = "\n".join(f"different {i}" for i in range(1, 30)) + "\n"
    result = ledger.surface("repo:a.py", replacement)

    assert result.mode == "full"
    assert result.reason == "diff_not_smaller"
    assert result.content == replacement


def test_binary_content_falls_back_to_full(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    ledger.surface("repo:bin", "text before\n")
    result = ledger.surface("repo:bin", "text\x00after\n")

    assert result.mode == "full"
    assert result.reason == "not_text"


def test_too_large_to_diff_falls_back_to_full(tmp_path: Path) -> None:
    ledger = ReadLedger(tmp_path)
    ledger.surface("repo:a.py", "seed\n")
    big = "x\n" * 10_000
    result = ledger.surface("repo:a.py", big, max_diff_chars=100)

    assert result.mode == "full"
    assert result.reason == "too_large_to_diff"


def test_ledger_persists_across_instances(tmp_path: Path) -> None:
    ReadLedger(tmp_path).surface("repo:a.py", "persisted\n")
    reopened = ReadLedger(tmp_path)

    assert reopened.get("repo:a.py") == "persisted\n"
    # A fresh instance over the same root still sees the prior read.
    result = reopened.surface("repo:a.py", "persisted\n")
    assert result.mode == "unchanged"


def test_read_delta_enabled_env() -> None:
    assert read_delta_enabled({}) is True
    assert read_delta_enabled({"ALFRED_READ_DELTA": "0"}) is False
    assert read_delta_enabled({"ALFRED_READ_DELTA": "off"}) is False
    assert read_delta_enabled({"ALFRED_READ_DELTA": "1"}) is True


def test_delta_max_ratio_env() -> None:
    assert delta_max_ratio({}) == pytest.approx(0.5)
    assert delta_max_ratio({"ALFRED_READ_DELTA_MAX_RATIO": "0.25"}) == pytest.approx(0.25)
    # Invalid values fall back to the default.
    assert delta_max_ratio({"ALFRED_READ_DELTA_MAX_RATIO": "nope"}) == pytest.approx(0.5)


def test_ledger_root_isolation_per_firing(tmp_path: Path) -> None:
    override = ledger_root_for(
        None, env={"ALFRED_READ_LEDGER_DIR": str(tmp_path / "x"), "ALFRED_FIRING_ID": "f1"}
    )
    # The override is a base dir; the ledger is still scoped beneath it.
    assert override.parent == tmp_path / "x"

    root_a = ledger_root_for(
        tmp_path / "wt-a",
        env={"ALFRED_FIRING_ID": "f1"},
        state_root=tmp_path / "state",
    )
    root_b = ledger_root_for(
        tmp_path / "wt-b",
        env={"ALFRED_FIRING_ID": "f1"},
        state_root=tmp_path / "state",
    )
    # Two worktrees under one firing never share a ledger directory.
    assert root_a != root_b
    assert root_a.parent == tmp_path / "state" / "read-ledger"


def test_override_dir_is_scoped_per_firing(tmp_path: Path) -> None:
    override = str(tmp_path / "shared")
    # Two firings pointed at the same override dir get distinct ledgers.
    f1 = ledger_root_for(None, env={"ALFRED_READ_LEDGER_DIR": override, "ALFRED_FIRING_ID": "f1"})
    f2 = ledger_root_for(None, env={"ALFRED_READ_LEDGER_DIR": override, "ALFRED_FIRING_ID": "f2"})
    assert f1 != f2
    assert f1.parent == tmp_path / "shared"


def test_ledger_root_requires_firing_id(tmp_path: Path) -> None:
    # A firing id is mandatory: never invent a process- or content-shared scope
    # two firings could collide on. This holds even with the dir override set.
    with pytest.raises(ValueError):
        ledger_root_for(None, env={}, state_root=tmp_path / "state")
    with pytest.raises(ValueError):
        ledger_root_for(None, env={"ALFRED_READ_LEDGER_DIR": str(tmp_path / "x")})
    with pytest.raises(ValueError):
        ledger_root_for(None, env={"ALFRED_FIRING_ID": "  "}, state_root=tmp_path / "state")


def test_read_delta_available_strictly_requires_firing_id() -> None:
    # A firing id is the only thing that enables delta.
    assert read_delta_available({"ALFRED_FIRING_ID": "f1"}) is True
    # No firing id: disabled, even with an explicit ledger dir set.
    assert read_delta_available({}) is False
    assert read_delta_available({"ALFRED_READ_LEDGER_DIR": "/tmp/x"}) is False
    # Blank firing id does not count.
    assert read_delta_available({"ALFRED_FIRING_ID": "  "}) is False
    assert (
        read_delta_available({"ALFRED_FIRING_ID": "  ", "ALFRED_READ_LEDGER_DIR": "/tmp/x"})
        is False
    )

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_senior_dev():
    bin_dir = str(ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    spec = importlib.util.spec_from_file_location(
        "senior_dev_already_implemented", ROOT / "bin" / "senior-dev.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_already_implemented_can_close_when_base_contains_the_work(monkeypatch) -> None:
    monkeypatch.setenv("ALFRED_SENIOR_DEV_REPOS", "")
    senior_dev = _load_senior_dev()
    assert senior_dev._can_close_as_already_implemented(
        "[ALREADY-IMPLEMENTED] src/example.py:12", 0
    )


def test_recovery_commit_cannot_be_reported_as_already_shipped(monkeypatch) -> None:
    monkeypatch.setenv("ALFRED_SENIOR_DEV_REPOS", "")
    senior_dev = _load_senior_dev()
    assert not senior_dev._can_close_as_already_implemented(
        "[ALREADY-IMPLEMENTED] src/example.py:12", 1
    )


def test_unmarked_zero_commit_result_is_not_already_implemented(monkeypatch) -> None:
    monkeypatch.setenv("ALFRED_SENIOR_DEV_REPOS", "")
    senior_dev = _load_senior_dev()
    assert not senior_dev._can_close_as_already_implemented("[OK] no changes", 0)

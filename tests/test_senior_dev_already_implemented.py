from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_senior_dev(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred-home"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("ALFRED_SENIOR_DEV_REPOS", "")
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


def test_already_implemented_can_close_when_base_contains_the_work(monkeypatch, tmp_path) -> None:
    senior_dev = _load_senior_dev(monkeypatch, tmp_path)
    assert (
        senior_dev._already_implemented_disposition(
            "[ALREADY-IMPLEMENTED] src/example.py:12", [], "acme/repo#42"
        )
        == "shipped-on-base"
    )


def test_matching_recovery_commit_continues_to_pr_path(monkeypatch, tmp_path) -> None:
    senior_dev = _load_senior_dev(monkeypatch, tmp_path)
    disposition = senior_dev._already_implemented_disposition(
        "[ALREADY-IMPLEMENTED] src/example.py:12",
        ["fix: setup\n\nIssue: acme/repo#42"],
        "acme/repo#42",
    )
    assert disposition == "recover-current-issue"


def test_unrelated_recovery_commit_is_quarantined(monkeypatch, tmp_path) -> None:
    senior_dev = _load_senior_dev(monkeypatch, tmp_path)
    disposition = senior_dev._already_implemented_disposition(
        "[ALREADY-IMPLEMENTED] src/example.py:12",
        ["fix: other issue\n\nIssue: acme/repo#41"],
        "acme/repo#42",
    )
    assert disposition == "stale-ahead-work"


def test_mixed_recovery_commits_are_quarantined(monkeypatch, tmp_path) -> None:
    senior_dev = _load_senior_dev(monkeypatch, tmp_path)
    disposition = senior_dev._already_implemented_disposition(
        "[ALREADY-IMPLEMENTED] src/example.py:12",
        ["Issue: acme/repo#42", "Issue: acme/repo#41"],
        "acme/repo#42",
    )
    assert disposition == "stale-ahead-work"


def test_unmarked_zero_commit_result_is_not_already_implemented(monkeypatch, tmp_path) -> None:
    senior_dev = _load_senior_dev(monkeypatch, tmp_path)
    assert (
        senior_dev._already_implemented_disposition("[OK] no changes", [], "acme/repo#42")
        == "not-marked"
    )

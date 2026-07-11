"""Tests for the automerge sweeper's GitHub-native gate mode wiring."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_automerge(monkeypatch: pytest.MonkeyPatch, env: dict | None = None):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    monkeypatch.setenv("GH_ORG", "acme")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("automerge", ROOT / "bin" / "automerge.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("automerge", None)
    spec.loader.exec_module(module)
    return module


def test_require_approval_defaults_on(monkeypatch):
    monkeypatch.delenv("ALFRED_MERGE_REQUIRE_APPROVAL", raising=False)
    automerge = load_automerge(monkeypatch)
    assert automerge.REQUIRE_APPROVAL is True
    assert automerge.MIN_APPROVALS == 1


def test_require_approval_can_be_disabled(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_REQUIRE_APPROVAL": "0"})
    assert automerge.REQUIRE_APPROVAL is False


def test_min_approvals_reads_env_and_floors_at_one(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "0"})
    assert automerge.MIN_APPROVALS == 1
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "2"})
    assert automerge.MIN_APPROVALS == 2


def test_merge_via_gate_merges_when_gate_passes(monkeypatch):
    automerge = load_automerge(monkeypatch)
    from merge_gate import CheckRun, GateSnapshot, Review, ReviewThread

    snap = GateSnapshot(
        state="OPEN",
        head_sha="a" * 40,
        review_decision="APPROVED",
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z"),),
        review_threads=(ReviewThread(True, "x.py", "operator"),),
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        checks=(CheckRun("ci", "SUCCESS"),),
        errors=(),
    )
    monkeypatch.setattr(automerge, "collect_snapshot", lambda slug, num: snap)
    captured = {}

    def _guarded(slug, num, head, delete_branch=True):
        captured["args"] = (slug, num, head)
        return True, "merged"

    monkeypatch.setattr(automerge, "guarded_squash_merge", _guarded)
    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})
    assert ok is True
    assert reason == "merged"
    # GH_ORG is bound into automerge at agent_runner import time; assert against
    # the value the module actually resolved rather than a hard-coded org.
    assert captured["args"] == (f"{automerge.GH_ORG}/widget", 12, "a" * 40)


def test_merge_via_gate_skips_when_gate_fails(monkeypatch):
    automerge = load_automerge(monkeypatch)
    from merge_gate import GateSnapshot

    snap = GateSnapshot(
        state="OPEN",
        head_sha="a" * 40,
        review_decision="CHANGES_REQUESTED",
        reviews=(),
        review_threads=(),
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        checks=(),
        errors=(),
    )
    monkeypatch.setattr(automerge, "collect_snapshot", lambda slug, num: snap)
    called = {"n": 0}

    def _guarded(*a, **kw):
        called["n"] += 1
        return True, "merged"

    monkeypatch.setattr(automerge, "guarded_squash_merge", _guarded)
    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})
    assert ok is False
    assert called["n"] == 0
    assert "changes requested" in reason

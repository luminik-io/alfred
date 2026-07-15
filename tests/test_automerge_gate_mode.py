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


def test_blank_require_approval_defaults_on(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_REQUIRE_APPROVAL": ""})
    assert automerge.REQUIRE_APPROVAL is True


def test_require_approval_can_be_disabled(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_REQUIRE_APPROVAL": "0"})
    assert automerge.REQUIRE_APPROVAL is False


def test_invalid_require_approval_fails_closed(monkeypatch):
    automerge = load_automerge(
        monkeypatch,
        {
            "ALFRED_MERGE_REQUIRE_APPROVAL": "sometimes",
            "ALFRED_MERGE_REQUIRED_EXTERNAL_REVIEWS": "codex",
        },
    )
    monkeypatch.setattr(
        automerge,
        "collect_snapshot",
        lambda *args, **kwargs: pytest.fail("GitHub must not be called"),
    )

    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})

    assert ok is False
    assert "must be true or false" in reason


def test_min_approvals_reads_env_and_rejects_invalid_values(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "0"})
    assert automerge.MIN_APPROVALS is None
    assert "integer >= 1" in automerge.MIN_APPROVALS_ERROR
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "two"})
    assert automerge.MIN_APPROVALS is None
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "2"})
    assert automerge.MIN_APPROVALS == 2


def test_invalid_min_approvals_blocks_gate_without_github_calls(monkeypatch):
    automerge = load_automerge(monkeypatch, {"ALFRED_MERGE_MIN_APPROVALS": "two"})
    monkeypatch.setattr(
        automerge,
        "collect_snapshot",
        lambda *args, **kwargs: pytest.fail("GitHub must not be called"),
    )
    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})
    assert ok is False
    assert "invalid merge-gate config" in reason


def test_external_gate_honors_disabled_human_approval(monkeypatch):
    automerge = load_automerge(
        monkeypatch,
        {
            "ALFRED_MERGE_REQUIRE_APPROVAL": "0",
            "ALFRED_MERGE_REQUIRED_EXTERNAL_REVIEWS": "codex",
        },
    )
    from merge_gate import CheckRun, ExternalReviewEvidence, GateSnapshot, ReviewThread

    head = "a" * 40
    snap = GateSnapshot(
        state="OPEN",
        head_sha=head,
        review_decision=None,
        reviews=(),
        review_threads=(ReviewThread(True, "x.py", "codex"),),
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        checks=(CheckRun("ci", "SUCCESS"),),
        errors=(),
        external_reviews=(
            ExternalReviewEvidence(
                "chatgpt-codex-connector[bot]",
                "Didn't find any major issues.",
                "2026-07-11T10:00:00Z",
                head,
            ),
        ),
    )
    monkeypatch.setattr(automerge, "collect_snapshot", lambda slug, num, **kw: snap)
    monkeypatch.setattr(
        automerge,
        "rechecked_squash_merge",
        lambda *args, **kwargs: (True, "merged"),
    )

    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})

    assert ok is True
    assert reason == "merged"


def test_merge_via_gate_merges_when_gate_passes(monkeypatch):
    automerge = load_automerge(monkeypatch)
    from merge_gate import CheckRun, GateSnapshot, Review, ReviewThread

    snap = GateSnapshot(
        state="OPEN",
        head_sha="a" * 40,
        review_decision="APPROVED",
        reviews=(Review("operator", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),),
        review_threads=(ReviewThread(True, "x.py", "operator"),),
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        checks=(CheckRun("ci", "SUCCESS"),),
        errors=(),
    )
    monkeypatch.setattr(automerge, "collect_snapshot", lambda slug, num: snap)
    captured = {}

    def _guarded(slug, num, head, **kwargs):
        captured["args"] = (slug, num, head)
        captured["policy"] = (
            kwargs["min_approvals"],
            kwargs["required_external_reviews"],
        )
        captured["delete_branch"] = kwargs["delete_branch"]
        return True, "merged"

    monkeypatch.setattr(automerge, "rechecked_squash_merge", _guarded)
    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})
    assert ok is True
    assert reason == "merged"
    # GH_ORG is bound into automerge at agent_runner import time; assert against
    # the value the module actually resolved rather than a hard-coded org.
    assert captured["args"] == (f"{automerge.GH_ORG}/widget", 12, "a" * 40)
    assert captured["policy"] == (1, ())
    assert captured["delete_branch"] is True


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

    monkeypatch.setattr(automerge, "rechecked_squash_merge", _guarded)
    ok, reason, _title = automerge._merge_via_gate("widget", {"number": 12, "title": "T"})
    assert ok is False
    assert called["n"] == 0
    assert "changes requested" in reason

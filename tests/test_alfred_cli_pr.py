"""Tests for the ``alfred pr check`` / ``alfred pr merge`` CLI wiring."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "alfred"
LIB = REPO_ROOT / "lib"
sys.path.insert(0, str(LIB))

import merge_gate  # noqa: E402
from merge_gate import CheckRun, GateSnapshot, Review, ReviewThread  # noqa: E402


@pytest.fixture()
def cli_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("GH_ORG", "acme")
    loader = SourceFileLoader("alfred_cli_pr", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_cli_pr"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mergeable_snapshot(**overrides) -> GateSnapshot:
    base = {
        "state": "OPEN",
        "head_sha": "a" * 40,
        "review_decision": "APPROVED",
        "reviews": (Review("operator", "APPROVED", "2026-07-11T10:00:00Z", "a" * 40),),
        "review_threads": (ReviewThread(True, "x.py", "operator"),),
        "merge_state_status": "CLEAN",
        "mergeable": "MERGEABLE",
        "checks": (CheckRun("ci", "SUCCESS"),),
        "errors": (),
    }
    base.update(overrides)
    return GateSnapshot(**base)


def test_pr_check_passes_returns_zero(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    rc = cli_module.main(["pr", "check", "7", "--repo", "acme/widget"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "MERGEABLE" in out
    assert "PASS" in out


def test_pr_check_failing_returns_one(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(review_decision="CHANGES_REQUESTED"),
    )
    rc = cli_module.main(["pr", "check", "7", "--repo", "acme/widget"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NOT MERGEABLE" in out


def test_pr_check_honors_disabled_human_approval(cli_module, monkeypatch, capsys):
    monkeypatch.setenv("ALFRED_MERGE_REQUIRE_APPROVAL", "0")
    seen = {}

    def _gate(repo, number, **kwargs):
        seen["min_approvals"] = kwargs["min_approvals"]
        snap = _mergeable_snapshot(review_decision=None, reviews=())
        return snap, merge_gate.evaluate_gate(snap, min_approvals=kwargs["min_approvals"])

    monkeypatch.setattr(merge_gate, "gate_pull_request", _gate)

    rc = cli_module.main(["pr", "check", "7", "--repo", "acme/widget"])

    assert rc == 0
    assert seen["min_approvals"] == 0


def test_pr_check_json_output(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    rc = cli_module.main(["pr", "check", "7", "--repo", "acme/widget", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["mergeable"] is True
    assert payload["repo"] == "acme/widget"
    assert payload["number"] == 7
    assert any(c["key"] == "approved" for c in payload["conditions"])


def test_pr_check_resolves_repo_from_gh_org(cli_module, monkeypatch, capsys):
    seen = {}

    def _collect(repo, number, **kw):
        seen["repo"] = repo
        return _mergeable_snapshot()

    monkeypatch.setattr(merge_gate, "collect_snapshot", _collect)
    rc = cli_module.main(["pr", "check", "7", "--repo", "widget"])
    assert rc == 0
    assert seen["repo"] == "acme/widget"


def test_pr_check_defaults_to_current_checkout_under_gh_org(cli_module, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="git@github.com:other-owner/widget.git\n", stderr=""
        ),
    )

    def _collect(repo, number, **kw):
        seen["repo"] = repo
        return _mergeable_snapshot()

    monkeypatch.setattr(merge_gate, "collect_snapshot", _collect)
    rc = cli_module.main(["pr", "check", "7"])
    assert rc == 0
    assert seen["repo"] == "acme/widget"


def test_pr_merge_gate_pass_calls_guarded_merge(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    calls = {}

    def _merge(repo, number, head_sha, **kw):
        calls["args"] = (repo, number, head_sha)
        calls["delete_branch"] = kw.get("delete_branch")
        return True, "merged"

    monkeypatch.setattr(merge_gate, "guarded_squash_merge", _merge)
    rc = cli_module.main(["pr", "merge", "7", "--repo", "acme/widget"])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls["args"] == ("acme/widget", 7, "a" * 40)
    assert calls["delete_branch"] is True
    assert "Merged" in out


def test_pr_merge_gate_fail_does_not_merge(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(
            review_threads=(ReviewThread(False, "y.py", "reviewer"),)
        ),
    )
    called = {"n": 0}

    def _merge(*a, **kw):
        called["n"] += 1
        return True, "merged"

    monkeypatch.setattr(merge_gate, "guarded_squash_merge", _merge)
    rc = cli_module.main(["pr", "merge", "7", "--repo", "acme/widget"])
    assert rc == 1
    assert called["n"] == 0


def test_pr_merge_no_delete_branch_flag(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    calls = {}

    def _merge(repo, number, head_sha, **kw):
        calls["delete_branch"] = kw.get("delete_branch")
        return True, "merged"

    monkeypatch.setattr(merge_gate, "guarded_squash_merge", _merge)
    rc = cli_module.main(["pr", "merge", "7", "--repo", "acme/widget", "--no-delete-branch"])
    assert rc == 0
    assert calls["delete_branch"] is False


def test_pr_merge_json_emits_one_parseable_document(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    monkeypatch.setattr(
        merge_gate,
        "guarded_squash_merge",
        lambda *args, **kwargs: (True, "merged"),
    )
    rc = cli_module.main(["pr", "merge", "7", "--repo", "acme/widget", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["merged"] is True
    assert "Merged " not in captured.out


def test_pr_merge_defaults_to_current_checkout_under_gh_org(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="https://github.com/other-owner/widget.git\n", stderr=""
        ),
    )
    monkeypatch.setattr(
        merge_gate,
        "collect_snapshot",
        lambda repo, number, **kw: _mergeable_snapshot(),
    )
    calls = {}

    def _merge(repo, number, head_sha, **kwargs):
        calls["repo"] = repo
        return True, "merged"

    monkeypatch.setattr(merge_gate, "guarded_squash_merge", _merge)
    rc = cli_module.main(["pr", "merge", "7"])
    assert rc == 0
    assert calls["repo"] == "acme/widget"


def test_pr_min_approvals_reads_env(cli_module, monkeypatch):
    monkeypatch.setenv("ALFRED_MERGE_MIN_APPROVALS", "3")
    assert cli_module._pr_min_approvals() == 3
    for raw in ("0", "junk"):
        monkeypatch.setenv("ALFRED_MERGE_MIN_APPROVALS", raw)
        with pytest.raises(ValueError):
            cli_module._pr_min_approvals()


def test_pr_command_rejects_invalid_min_approvals_before_gate(cli_module, monkeypatch, capsys):
    monkeypatch.setenv("ALFRED_MERGE_MIN_APPROVALS", "two")
    called = {"count": 0}

    def _collect(*args, **kwargs):
        called["count"] += 1
        return _mergeable_snapshot()

    monkeypatch.setattr(merge_gate, "collect_snapshot", _collect)
    rc = cli_module.main(["pr", "check", "7", "--repo", "acme/widget"])
    captured = capsys.readouterr()
    assert rc == 2
    assert called["count"] == 0
    assert "integer >= 1" in captured.err

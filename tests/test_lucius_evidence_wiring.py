"""Lucius must attach honest verification evidence to the PR body.

These tests exercise the runner-side glue (not the pure formatter, which has
its own suite): pre-push capture -> test evidence, diff stat parsing, the
default-on gate, and the opt-in screenshot path. Every subprocess and engine
call is stubbed so nothing runs a browser, an LLM, or git.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_bin_module(name: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "bin" / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lucius(monkeypatch):
    monkeypatch.setenv("GH_ORG", "myorg")
    return load_bin_module("lucius.py", monkeypatch)


def test_test_evidence_from_passed_pre_push(lucius, monkeypatch):
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    pre = lucius.PrePushResult(
        ok=True,
        command="uv run pytest",
        stdout="===== 12 passed in 3.4s =====",
    )
    ev = lucius._test_evidence_from_pre_push(pre)
    assert ev.ran is True
    assert ev.ok is True
    assert ev.command == "uv run pytest"
    assert "12 passed" in ev.summary


def test_test_evidence_no_command_is_not_ran(lucius):
    pre = lucius.PrePushResult(ok=True, command="")
    ev = lucius._test_evidence_from_pre_push(pre)
    assert ev.ran is False
    assert "no pre-push command" in ev.reason


def test_test_evidence_none_is_honest(lucius):
    ev = lucius._test_evidence_from_pre_push(None)
    assert ev.ran is False
    assert "not captured" in ev.reason


def test_diff_stat_parses_numstat(lucius, monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["git", "diff", "--numstat"]
        return subprocess.CompletedProcess(
            cmd, 0, stdout="10\t2\tlib/a.py\n5\t0\tbin/b.py\n-\t-\tassets/logo.png\n"
        )

    monkeypatch.setattr(lucius, "run", fake_run)
    stat = lucius._diff_stat(Path("/x"), "origin/main")
    assert stat.files_changed == 3
    assert stat.insertions == 15
    assert stat.deletions == 2
    assert "lib/a.py" in stat.files


def test_evidence_block_empty_when_gate_off_and_no_preview(lucius, monkeypatch):
    monkeypatch.setenv("ALFRED_PR_EVIDENCE", "0")
    monkeypatch.setattr(lucius, "PREVIEW_CONFIG", {})
    block = lucius._verification_evidence_block(
        "backend", {"body": "x"}, Path("/x"), "br", "origin/main", "fid", None
    )
    assert block == ""


def test_gate_off_still_captures_configured_screenshots(lucius, monkeypatch):
    # Screenshots are opt-in per repo and independent of ALFRED_PR_EVIDENCE:
    # gate off + configured preview -> screenshots-only evidence block.
    monkeypatch.setenv("ALFRED_PR_EVIDENCE", "0")
    shots = lucius.ScreenshotEvidence(
        attempted=True, ok=True, after_path=".alfred/evidence/fid/after.png", route="/dash"
    )
    seen: list[tuple] = []

    def fake_capture(repo, wt, branch, firing_id):
        seen.append((repo, branch, firing_id))
        return shots

    monkeypatch.setattr(lucius, "_capture_screenshot_evidence", fake_capture)
    block = lucius._verification_evidence_block(
        "frontend", {"body": "x"}, Path("/x"), "br", "origin/main", "fid", None
    )
    assert seen == [("frontend", "br", "fid")]
    assert "### Screenshots" in block
    assert ".alfred/evidence/fid/after.png" in block
    # Core tiers are a disabled feature, not missing evidence.
    assert "### Tests" not in block
    assert "### Diff" not in block
    assert "### Acceptance criteria" not in block


def test_self_assessment_counts_against_spend(lucius, monkeypatch):
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["git", "diff"]
        return subprocess.CompletedProcess(cmd, 0, stdout="+ real diff content\n")

    monkeypatch.setattr(lucius, "run", fake_run)

    class _FakeResult:
        num_turns = 3
        cost_usd = 0.42
        result_text = '{"criteria": [{"index": 0, "met": true}]}'

    monkeypatch.setattr(lucius, "invoke_agent_engine", lambda *a, **kw: (_FakeResult(), "claude"))

    class _FakeSpend:
        def __init__(self):
            self.increments: list[dict] = []

        def increment(self, **kwargs):
            self.increments.append(kwargs)

    spend = _FakeSpend()
    assessment = lucius._build_self_assessment(
        "backend",
        {"body": "## Acceptance criteria\n- [ ] It works\n"},
        Path("/x"),
        "origin/main",
        "fid",
        spend=spend,
    )
    assert assessment.produced is True
    assert assessment.criteria[0].met is True
    assert spend.increments == [{"turns_today": 3, "cost_usd_today": 0.42}]


def test_evidence_block_assembles_when_gate_on(lucius, monkeypatch):
    monkeypatch.setenv("ALFRED_PR_EVIDENCE", "1")
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "diff", "--numstat"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="3\t1\tlib/a.py\n")
        if cmd[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="")  # empty -> skip self-assess LLM
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(lucius, "run", fake_run)
    # No preview config for this repo -> screenshots omitted.
    monkeypatch.setattr(lucius, "PREVIEW_CONFIG", {})

    pre = lucius.PrePushResult(ok=True, command="pytest", stdout="== 1 passed in 0.1s ==")
    block = lucius._verification_evidence_block(
        "backend",
        {"body": "## Acceptance criteria\n- [ ] It works\n"},
        Path("/x"),
        "br",
        "origin/main",
        "fid",
        pre,
    )
    assert "## Verification evidence" in block
    assert "1 passed" in block
    assert "1 file(s) changed, +3 / -1" in block
    assert "### Screenshots" not in block


def test_evidence_block_swallows_errors(lucius, monkeypatch):
    monkeypatch.setenv("ALFRED_PR_EVIDENCE", "1")
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)

    def boom(cmd, **kwargs):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(lucius, "run", boom)
    block = lucius._verification_evidence_block(
        "backend", {"body": "x"}, Path("/x"), "br", "origin/main", "fid", None
    )
    # Never raises; still emits the heading with an honest note.
    assert "## Verification evidence" in block
    assert "errored" in block


def test_load_preview_config_reads_toml(lucius, monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "lucius.toml").write_text(
        "[preview.frontend]\n"
        'start_cmd = "npm run dev"\n'
        'url = "http://localhost:5173"\n'
        'route = "/dashboard"\n'
    )
    monkeypatch.setattr(lucius, "ALFRED_HOME", tmp_path)
    monkeypatch.setattr(lucius, "LUCIUS_REPOS", ["frontend", "backend"])
    cfg = lucius._load_preview_config("lucius")
    assert cfg["frontend"].enabled is True
    assert cfg["frontend"].route == "/dashboard"
    assert cfg["backend"].enabled is False


def test_screenshot_evidence_skipped_without_config(lucius, monkeypatch):
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    monkeypatch.setattr(lucius, "PREVIEW_CONFIG", {"backend": lucius.PreviewConfig()})
    result = lucius._capture_screenshot_evidence("backend", Path("/x"), "br", "fid")
    assert result is None

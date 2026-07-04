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
    return load_bin_module("senior-dev.py", monkeypatch)


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


def test_test_evidence_dry_run_does_not_claim_passed(lucius, monkeypatch):
    # In dry-run run_pre_push_checks returns ok=True with a command but never
    # executes it. Evidence must NOT render "Pre-push checks passed".
    monkeypatch.setattr(lucius, "is_dry_run", lambda: True)
    pre = lucius.PrePushResult(ok=True, command="uv run pytest")
    ev = lucius._test_evidence_from_pre_push(pre)
    assert ev.ran is False
    assert ev.command == "uv run pytest"
    assert ev.reason == "not run (dry-run)"
    from verification_evidence import EvidenceInputs, build_evidence_block

    md = build_evidence_block(EvidenceInputs(test=ev))
    assert "passed" not in md.lower()
    assert "not run (dry-run)" in md


def test_diff_stat_parses_numstat_and_excludes_evidence(lucius, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        assert cmd[:3] == ["git", "diff", "--numstat"]
        # The evidence tree is excluded so committed screenshots never inflate
        # the code-change summary.
        assert any(part.startswith(":(exclude)") and "evidence" in part for part in cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="10\t2\tlib/a.py\n5\t0\tbin/b.py\n-\t-\tassets/logo.png\n"
        )

    monkeypatch.setattr(lucius, "run", fake_run)
    stat = lucius._diff_stat(Path("/x"), "origin/main")
    assert stat.files_changed == 3
    assert stat.insertions == 15
    assert stat.deletions == 2
    assert "lib/a.py" in stat.files


def test_evidence_block_computes_diff_after_screenshots(lucius, monkeypatch):
    # P1: screenshots commit evidence onto the branch, so the diff must be
    # computed AFTER capture, not before.
    monkeypatch.setenv("ALFRED_PR_EVIDENCE", "1")
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    order: list[str] = []

    def fake_capture(repo, wt, branch, firing_id, base_ref):
        order.append("screenshots")
        return None

    def fake_diff(wt, base_ref):
        order.append("diff")
        return lucius.DiffStat(files_changed=1, insertions=1, deletions=0, files=("a.py",))

    def fake_assess(repo, issue, wt, base_ref, firing_id, spend=None):
        order.append("assess")
        return lucius.SelfAssessment(produced=True, criteria=())

    monkeypatch.setattr(lucius, "_capture_screenshot_evidence", fake_capture)
    monkeypatch.setattr(lucius, "_diff_stat", fake_diff)
    monkeypatch.setattr(lucius, "_build_self_assessment", fake_assess)

    lucius._verification_evidence_block(
        "frontend", {"body": "x"}, Path("/x"), "br", "origin/main", "fid", None
    )
    assert order.index("screenshots") < order.index("diff")


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

    def fake_capture(repo, wt, branch, firing_id, base_ref):
        seen.append((repo, branch, firing_id, base_ref))
        return shots

    monkeypatch.setattr(lucius, "_capture_screenshot_evidence", fake_capture)
    block = lucius._verification_evidence_block(
        "frontend", {"body": "x"}, Path("/x"), "br", "origin/main", "fid", None
    )
    assert seen == [("frontend", "br", "fid", "origin/main")]
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
    result = lucius._capture_screenshot_evidence("backend", Path("/x"), "br", "fid", "origin/main")
    assert result is None


def test_screenshot_evidence_prepares_and_cleans_base_worktree(lucius, monkeypatch):
    # A configured preview builds a base worktree, passes it as base_dir, and
    # removes it afterward even on the happy path.
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    monkeypatch.setattr(
        lucius,
        "PREVIEW_CONFIG",
        {
            "frontend": lucius.PreviewConfig(
                start_cmd="npm run dev", url="http://localhost:5173", route="/"
            )
        },
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    captured: dict = {}

    def fake_capture(wt, config, firing_id, base_dir=None, **kwargs):
        captured["base_dir"] = base_dir
        return lucius.ScreenshotEvidence(
            attempted=True,
            ok=True,
            before_path=".alfred/evidence/fid/before.png",
            after_path=".alfred/evidence/fid/after.png",
            route="/",
        )

    monkeypatch.setattr(lucius, "run", fake_run)
    monkeypatch.setattr(lucius, "capture_screenshots", fake_capture)
    monkeypatch.setattr(
        lucius, "push_current_branch", lambda wt, branch: subprocess.CompletedProcess([], 0)
    )

    result = lucius._capture_screenshot_evidence(
        "frontend", Path("/wt"), "br", "fid", "origin/main"
    )
    assert result.ok is True
    # base worktree was created and given to capture_screenshots
    assert captured["base_dir"] is not None
    assert any(c[:3] == ["git", "worktree", "add"] for c in calls)
    # and cleaned up
    assert any(c[:3] == ["git", "worktree", "remove"] for c in calls)


def test_before_add_failure_drops_reference_not_link(lucius, monkeypatch):
    # If `git add` of the before-image fails, the commit of after.png can still
    # succeed - but we must NOT keep before_path, or the PR would link an
    # uncommitted baseline. Drop it and report before_reason honestly.
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    monkeypatch.setattr(
        lucius,
        "PREVIEW_CONFIG",
        {
            "frontend": lucius.PreviewConfig(
                start_cmd="npm run dev", url="http://localhost:5173", route="/"
            )
        },
    )

    def fake_run(cmd, **kwargs):
        # Fail only the before-image `git add`; everything else succeeds.
        if cmd[:2] == ["git", "add"] and cmd[-1].endswith("before.png"):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pathspec error")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_capture(wt, config, firing_id, base_dir=None, **kwargs):
        return lucius.ScreenshotEvidence(
            attempted=True,
            ok=True,
            before_path=".alfred/evidence/fid/before.png",
            after_path=".alfred/evidence/fid/after.png",
            route="/",
        )

    monkeypatch.setattr(lucius, "run", fake_run)
    monkeypatch.setattr(lucius, "capture_screenshots", fake_capture)
    monkeypatch.setattr(
        lucius, "push_current_branch", lambda wt, branch: subprocess.CompletedProcess([], 0)
    )

    result = lucius._capture_screenshot_evidence(
        "frontend", Path("/wt"), "br", "fid", "origin/main"
    )
    # after.png still landed, so overall capture succeeds...
    assert result.ok is True
    assert result.after_path == ".alfred/evidence/fid/after.png"
    # ...but the never-committed before-image is dropped, not linked.
    assert result.before_path == ""
    assert "failed to stage baseline" in result.before_reason


def test_before_add_success_keeps_reference(lucius, monkeypatch):
    # Sanity counterpart: when the before `git add` succeeds, the reference
    # survives the commit.
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    monkeypatch.setattr(
        lucius,
        "PREVIEW_CONFIG",
        {
            "frontend": lucius.PreviewConfig(
                start_cmd="npm run dev", url="http://localhost:5173", route="/"
            )
        },
    )
    monkeypatch.setattr(
        lucius, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    )
    monkeypatch.setattr(
        lucius,
        "capture_screenshots",
        lambda wt, config, firing_id, base_dir=None, **kw: lucius.ScreenshotEvidence(
            attempted=True,
            ok=True,
            before_path=".alfred/evidence/fid/before.png",
            after_path=".alfred/evidence/fid/after.png",
            route="/",
        ),
    )
    monkeypatch.setattr(
        lucius, "push_current_branch", lambda wt, branch: subprocess.CompletedProcess([], 0)
    )
    result = lucius._capture_screenshot_evidence(
        "frontend", Path("/wt"), "br", "fid", "origin/main"
    )
    assert result.before_path == ".alfred/evidence/fid/before.png"
    assert result.before_reason == ""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import verification_evidence as ve  # noqa: E402
from verification_evidence import (  # noqa: E402
    CriterionAssessment,
    DiffStat,
    EvidenceInputs,
    PreviewConfig,
    ScreenshotEvidence,
    SelfAssessment,
    TestEvidence,
    assessment_prompt,
    build_evidence_block,
    capture_screenshots,
    evidence_enabled,
    extract_acceptance_criteria,
    load_preview_config,
    parse_assessment_response,
    parse_test_summary,
)

# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_evidence_enabled_defaults_on():
    assert evidence_enabled({}) is True
    assert evidence_enabled({"ALFRED_PR_EVIDENCE": "1"}) is True


def test_evidence_enabled_off_variants():
    for val in ("0", "false", "no", "off", "", "FALSE"):
        assert evidence_enabled({"ALFRED_PR_EVIDENCE": val}) is False


# ---------------------------------------------------------------------------
# Test summary parsing
# ---------------------------------------------------------------------------


def test_parse_test_summary_pytest():
    out = "===== 42 passed, 3 skipped in 12.34s ====="
    assert parse_test_summary(out) == "pytest: 42 passed, 3 skipped in 12.34s"


def test_parse_test_summary_pytest_with_failures():
    out = "===== 5 failed, 40 passed in 9.1s ====="
    summary = parse_test_summary(out)
    assert summary.startswith("pytest:")
    assert "40 passed" in summary
    assert "5 failed" in summary


def test_parse_test_summary_jest():
    out = "Tests:       2 failed, 18 passed, 20 total"
    summary = parse_test_summary(out)
    assert summary.startswith("jest/vitest:")
    assert "18 passed" in summary
    assert "2 failed" in summary


def test_parse_test_summary_gradle():
    out = "BUILD SUCCESSFUL in 1m 3s"
    assert parse_test_summary(out) == "build successful"


def test_parse_test_summary_unrecognised_returns_empty():
    assert parse_test_summary("nothing familiar here") == ""


def test_parse_test_summary_never_raises_on_junk():
    # Regex over adversarial input must not blow up.
    assert isinstance(parse_test_summary("{" * 5000), str)


def test_parse_test_summary_linear_on_redos_shaped_input():
    # CodeQL flagged the earlier pytest-line regex for exponential
    # backtracking on "=0 000 000 ..." shaped strings. The rewritten lazy
    # pattern must stay linear: this call returns (quickly) instead of
    # hanging the runner.
    import time

    adversarial = "=0 " + "000 " * 20000
    start = time.monotonic()
    result = parse_test_summary(adversarial)
    assert time.monotonic() - start < 5.0
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Acceptance criteria extraction
# ---------------------------------------------------------------------------


def test_extract_criteria_from_explicit_section():
    body = (
        "Some intro.\n\n"
        "## Acceptance criteria\n"
        "- [ ] Users can log in\n"
        "- [x] Errors are logged\n"
        "- Password reset works\n\n"
        "## Notes\n"
        "- unrelated bullet\n"
    )
    criteria = extract_acceptance_criteria(body)
    assert criteria == ["Users can log in", "Errors are logged", "Password reset works"]


def test_extract_criteria_falls_back_to_checkbox_list():
    body = "No heading here.\n- [ ] Do the thing\n- [x] Do the other thing\n- plain bullet\n"
    criteria = extract_acceptance_criteria(body)
    assert criteria == ["Do the thing", "Do the other thing"]


def test_extract_criteria_empty_when_none():
    assert extract_acceptance_criteria("just prose, no lists") == []


def test_extract_criteria_respects_limit():
    body = "## Acceptance\n" + "".join(f"- item {i}\n" for i in range(30))
    assert len(extract_acceptance_criteria(body, limit=5)) == 5


# ---------------------------------------------------------------------------
# Self-assessment parsing
# ---------------------------------------------------------------------------


def test_parse_assessment_happy_path():
    criteria = ["A", "B"]
    resp = (
        '{"criteria": [{"index": 0, "met": true, "note": "done"}, '
        '{"index": 1, "met": false, "note": "todo"}], "overall": "mostly"}'
    )
    result = parse_assessment_response(resp, criteria)
    assert result.produced is True
    assert result.criteria[0].met is True
    assert result.criteria[0].note == "done"
    assert result.criteria[1].met is False
    assert result.overall_note == "mostly"


def test_parse_assessment_embedded_in_prose():
    criteria = ["A"]
    resp = 'Here is my review:\n{"criteria": [{"index": 0, "met": true}]}\nThanks!'
    result = parse_assessment_response(resp, criteria)
    assert result.produced is True
    assert result.criteria[0].met is True


def test_parse_assessment_missing_index_is_undetermined():
    criteria = ["A", "B"]
    resp = '{"criteria": [{"index": 0, "met": true}]}'
    result = parse_assessment_response(resp, criteria)
    assert result.criteria[0].met is True
    assert result.criteria[1].met is None  # not judged -> honest None


def test_parse_assessment_unparseable_marks_not_produced():
    result = parse_assessment_response("sorry I cannot", ["A"])
    assert result.produced is False
    assert "parseable" in result.reason
    # criteria preserved as undetermined so the block still lists them
    assert result.criteria[0].met is None


def test_parse_assessment_no_criteria_is_produced_empty():
    result = parse_assessment_response("{}", [])
    assert result.produced is True
    assert result.criteria == ()


def test_assessment_prompt_truncates_large_diff():
    prompt = assessment_prompt("x" * 100000, ["do it"], max_diff_chars=100)
    assert "diff truncated" in prompt
    assert "do it" in prompt


# ---------------------------------------------------------------------------
# Preview config
# ---------------------------------------------------------------------------


def test_load_preview_config_enabled():
    cfg = load_preview_config(
        {"start_cmd": "npm run dev", "url": "http://localhost:3000", "route": "/x"}
    )
    assert cfg.enabled is True
    assert cfg.route == "/x"


def test_load_preview_config_disabled_without_start_or_url():
    assert load_preview_config({"url": "http://x"}).enabled is False
    assert load_preview_config({"start_cmd": "npm run dev"}).enabled is False
    assert load_preview_config(None).enabled is False
    assert load_preview_config("nope").enabled is False


# ---------------------------------------------------------------------------
# Screenshot capture (fully stubbed - never launches a browser)
# ---------------------------------------------------------------------------


def test_capture_screenshots_disabled_config():
    shots = capture_screenshots(Path("/tmp"), PreviewConfig(), "fid")
    assert shots.attempted is False
    assert "not configured" in shots.reason


def test_capture_screenshots_success(tmp_path):
    cfg = PreviewConfig(
        start_cmd="run-server --port 5173",
        url="http://localhost:5173",
        route="/dash",
        screenshot_cmd="shot {url} {out}",
    )
    started: list[list[str]] = []
    slept: list[float] = []
    proc = _FakeProc()

    def fake_popen(cmd, **kwargs):
        started.append(cmd)
        return proc

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "shot"
        assert cmd[1] == "http://localhost:5173/dash"
        Path(cmd[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    shots = capture_screenshots(
        tmp_path, cfg, "fire123", run_cmd=fake_run, popen=fake_popen, sleep=slept.append
    )
    assert shots.ok is True
    assert shots.after_path == ".alfred/evidence/fire123/after.png"
    assert (tmp_path / shots.after_path).exists()
    assert shots.route == "/dash"
    assert started == [["run-server", "--port", "5173"]]
    assert slept  # boot grace period requested
    assert proc.terminated  # server always torn down


def test_capture_screenshots_shot_failure(tmp_path):
    cfg = PreviewConfig(
        start_cmd="run-server", url="http://localhost:5173", screenshot_cmd="shot {url} {out}"
    )
    proc = _FakeProc()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    shots = capture_screenshots(
        tmp_path, cfg, "f", run_cmd=fake_run, popen=lambda *a, **kw: proc, sleep=lambda s: None
    )
    assert shots.attempted is True
    assert shots.ok is False
    assert "failed" in shots.reason
    assert proc.terminated


def test_capture_screenshots_start_raises(tmp_path):
    cfg = PreviewConfig(start_cmd="run-server", url="http://x", screenshot_cmd="shot {url} {out}")

    def fake_popen(cmd, **kwargs):
        raise OSError("no such command")

    shots = capture_screenshots(tmp_path, cfg, "f", popen=fake_popen, sleep=lambda s: None)
    assert shots.ok is False
    assert "OSError" in shots.reason


def test_capture_screenshots_out_path_with_spaces_stays_one_token(tmp_path):
    # A worktree path containing spaces must reach the screenshot command as a
    # single argv entry, not be re-split into several tokens.
    worktree = tmp_path / "My Repo"
    worktree.mkdir()
    cfg = PreviewConfig(
        start_cmd="run-server", url="http://localhost:5173", screenshot_cmd="shot {url} {out}"
    )
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    shots = capture_screenshots(
        worktree,
        cfg,
        "f1",
        run_cmd=fake_run,
        popen=lambda *a, **kw: _FakeProc(),
        sleep=lambda s: None,
    )
    assert shots.ok is True
    assert len(seen) == 1
    assert seen[0] == ["shot", "http://localhost:5173", str(worktree / shots.after_path)]
    assert "My Repo" in seen[0][-1]


def test_terminate_kills_whole_process_group(monkeypatch):
    # start_cmd wrappers (npm run dev) fork the real server; teardown must
    # signal the process group, not just the wrapper.
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(ve.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    class _PidProc:
        pid = 4242

        def __init__(self):
            self.waited = False

        def wait(self, timeout=None):
            self.waited = True

    proc = _PidProc()
    ve._terminate(proc)
    import signal

    assert killed == [(4242, signal.SIGTERM)]
    assert proc.waited is True


def test_terminate_escalates_to_sigkill_when_group_survives(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(ve.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    class _StubbornProc:
        pid = 77

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="run-server", timeout=timeout)

    ve._terminate(_StubbornProc())
    import signal

    assert killed == [(77, signal.SIGTERM), (77, signal.SIGKILL)]


def test_terminate_falls_back_to_terminate_without_pid():
    proc = _FakeProc()
    ve._terminate(proc)
    assert proc.terminated is True


class _FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


# ---------------------------------------------------------------------------
# Markdown assembly
# ---------------------------------------------------------------------------


def test_build_block_full_and_honest():
    inputs = EvidenceInputs(
        test=TestEvidence(
            ran=True,
            command="uv run pytest",
            ok=True,
            summary="pytest: 10 passed in 2.0s",
            duration_s=2.0,
        ),
        diff=DiffStat(files_changed=2, insertions=30, deletions=4, files=("a.py", "b.py")),
        assessment=SelfAssessment(
            produced=True,
            criteria=(
                CriterionAssessment(text="Login works", met=True, note="added handler"),
                CriterionAssessment(text="Logout works", met=False),
                CriterionAssessment(text="Reset works", met=None),
            ),
            overall_note="two of three met",
        ),
        screenshots=ScreenshotEvidence(
            attempted=True, ok=True, after_path=".alfred/evidence/f/after.png", route="/"
        ),
    )
    md = build_evidence_block(inputs)
    assert md.startswith("## Verification evidence")
    assert "Pre-push checks passed" in md
    assert "2 file(s) changed, +30 / -4" in md
    assert "self-assessment" in md.lower()
    assert "[x] Login works" in md
    assert "[ ] Logout works" in md
    assert "[?] Reset works" in md
    assert "[`.alfred/evidence/f/after.png`]" in md


def test_build_block_missing_evidence_is_labelled_not_omitted():
    md = build_evidence_block(EvidenceInputs())
    assert "## Verification evidence" in md
    # every subsection present with an honest "not captured"
    assert md.count("not captured") >= 2
    assert "### Tests" in md
    assert "### Diff" in md
    assert "### Acceptance criteria" in md


def test_build_block_failed_tests_marked_failed():
    md = build_evidence_block(
        EvidenceInputs(
            test=TestEvidence(ran=True, command="pytest", ok=False, reason="exit 1"),
        )
    )
    assert "Pre-push checks FAILED" in md


def test_build_block_no_pre_push_command_is_honest():
    md = build_evidence_block(
        EvidenceInputs(test=TestEvidence(ran=False, reason="no pre-push command configured"))
    )
    assert "no pre-push command configured" in md
    assert "not captured" in md


def test_build_block_omits_screenshot_section_when_not_attempted():
    md = build_evidence_block(
        EvidenceInputs(test=TestEvidence(ran=True, command="pytest", ok=True))
    )
    assert "### Screenshots" not in md


def test_build_block_screenshot_failure_is_reported():
    md = build_evidence_block(
        EvidenceInputs(
            screenshots=ScreenshotEvidence(attempted=True, ok=False, reason="server never ready"),
        )
    )
    assert "### Screenshots" in md
    assert "server never ready" in md


def test_build_block_assessment_no_criteria_found():
    md = build_evidence_block(EvidenceInputs(assessment=SelfAssessment(produced=True, criteria=())))
    assert "no acceptance criteria found" in md


def test_build_block_always_ends_with_newline():
    md = build_evidence_block(EvidenceInputs())
    assert md.endswith("\n")


def test_build_block_core_off_keeps_screenshots_only():
    # ALFRED_PR_EVIDENCE=0 disables the core tiers as a feature, but a repo
    # that opted into screenshots still gets its screenshot evidence.
    md = build_evidence_block(
        EvidenceInputs(
            screenshots=ScreenshotEvidence(
                attempted=True, ok=True, after_path=".alfred/evidence/f/after.png", route="/"
            ),
            include_core=False,
        )
    )
    assert "## Verification evidence" in md
    assert "### Screenshots" in md
    assert "### Tests" not in md
    assert "### Diff" not in md
    assert "### Acceptance criteria" not in md


def test_build_block_core_off_and_no_screenshots_is_empty():
    assert build_evidence_block(EvidenceInputs(include_core=False)) == ""

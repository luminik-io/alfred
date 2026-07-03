"""Unit tests for ``alfred demo``.

The whole point of splitting the orchestration into ``lib/demo`` is that the
loop runs here with a SCRIPTED engine: no real ``claude`` call, no network,
deterministic. These tests drive the full plan/approve/build/review/fix/ship
sequence against the real bundled sample repo and assert the honest branches.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from demo import (  # noqa: E402
    DEMO_STEPS,
    DemoAborted,
    DemoEngineError,
    EngineCall,
    EngineOutcome,
    materialize_sample_repo,
    run_demo,
)
from demo.orchestrator import (  # noqa: E402
    REVIEW_BLOCK_SENTINEL,
    REVIEW_PASS_SENTINEL,
)
from demo.presenter import Presenter  # noqa: E402


def load_demo_runner():
    loader = importlib.machinery.SourceFileLoader(
        "alfred_demo_for_test", str(ROOT / "bin/alfred-demo.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_cli_module():
    loader = importlib.machinery.SourceFileLoader("alfred_cli_for_test", str(ROOT / "bin/alfred"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Scripted engine
# ---------------------------------------------------------------------------


class ScriptedEngine:
    """A fake engine that answers each demo step from a fixed script.

    It also performs the real file edits a build/fix step would make, so the
    ship step produces a genuine diff. This keeps the test honest: the loop
    still commits real changes, it just does not call an LLM to author them.
    """

    def __init__(self, *, catch_bug: bool = True, fail_step: str | None = None) -> None:
        self.catch_bug = catch_bug
        self.fail_step = fail_step
        self.calls: list[EngineCall] = []

    def __call__(self, call: EngineCall) -> EngineOutcome:
        self.calls.append(call)
        if call.step == self.fail_step:
            return EngineOutcome(success=False, text="", error_message="scripted failure")

        if call.step == "plan":
            return EngineOutcome(
                success=True,
                text="Add slugify(text): lowercase, hyphenate non-alphanumerics, strip hyphens.",
            )
        if call.step == "build":
            self._append_slugify(call.workdir)
            return EngineOutcome(success=True, text="[DEMO-BUILD-DONE] added slugify + tests")
        if call.step == "review":
            verdict = REVIEW_BLOCK_SENTINEL if self.catch_bug else REVIEW_PASS_SENTINEL
            finding = (
                "titlecase collapses consecutive spaces because it splits and rejoins on a "
                'single space; "a  b" returns "A B".'
            )
            return EngineOutcome(success=True, text=f"{finding}\n{verdict}")
        if call.step == "fix":
            self._fix_titlecase(call.workdir)
            return EngineOutcome(success=True, text="[DEMO-FIX-DONE] preserve whitespace runs")
        raise AssertionError(f"unexpected step {call.step}")

    @staticmethod
    def _append_slugify(workdir: Path) -> None:
        lib = workdir / "textkit.py"
        lib.write_text(
            lib.read_text() + "\n\ndef slugify(text: str) -> str:\n"
            "    import re\n"
            '    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")\n'
        )

    @staticmethod
    def _fix_titlecase(workdir: Path) -> None:
        lib = workdir / "textkit.py"
        text = lib.read_text()
        lib.write_text(text + "\n# fix: preserve whitespace runs in titlecase\n")


def _run(engine: ScriptedEngine, tmp_path: Path, *, approve=lambda _p: True):
    workdir = materialize_sample_repo(tmp_path / "textkit")
    events: list = []
    ticks = iter(range(1000))
    return (
        run_demo(
            engine=engine,
            events=events.append,
            approve=approve,
            workdir=workdir,
            timeout=30,
            clock=lambda: next(ticks),
        ),
        events,
    )


# ---------------------------------------------------------------------------
# Sample repo
# ---------------------------------------------------------------------------


def test_materialize_sample_repo_is_a_real_git_repo(tmp_path):
    workdir = materialize_sample_repo(tmp_path / "textkit")
    assert (workdir / "textkit.py").exists()
    assert (workdir / "test_textkit.py").exists()
    assert (workdir / ".git").is_dir()


# ---------------------------------------------------------------------------
# Full loop, bug caught
# ---------------------------------------------------------------------------


def test_full_loop_catches_bug_and_ships(tmp_path):
    engine = ScriptedEngine(catch_bug=True)
    result, events = _run(engine, tmp_path)

    assert result.shipped is True
    assert result.bug_caught is True
    steps_seen = [e.step for e in events]
    for step in DEMO_STEPS:
        assert step in steps_seen, f"missing step {step}"
    # The fix step must have actually run when the bug was caught.
    assert any(c.step == "fix" for c in engine.calls)
    # The ship summary is built from a real diff, not fabricated.
    assert "files changed" in result.diff_summary
    assert "textkit.py" in result.diff_summary


def test_review_verdict_drives_fix_branch(tmp_path):
    engine = ScriptedEngine(catch_bug=False)
    result, events = _run(engine, tmp_path)

    assert result.shipped is True
    assert result.bug_caught is False
    # When the reviewer approves, no fix engine call is made.
    assert not any(c.step == "fix" for c in engine.calls)
    fix_done = [e for e in events if e.step == "fix" and e.kind == "done"]
    assert fix_done and "no fix was needed" in fix_done[0].text


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def test_declining_gate_aborts_without_building(tmp_path):
    engine = ScriptedEngine()
    with pytest.raises(DemoAborted):
        _run(engine, tmp_path, approve=lambda _p: False)
    # Only the plan call ran; nothing was built.
    assert [c.step for c in engine.calls] == ["plan"]


# ---------------------------------------------------------------------------
# Honest failure
# ---------------------------------------------------------------------------


def test_engine_failure_stops_honestly(tmp_path):
    engine = ScriptedEngine(fail_step="build")
    with pytest.raises(DemoEngineError) as exc_info:
        _run(engine, tmp_path)
    assert exc_info.value.step == "build"


def test_unsuccessful_engine_result_is_a_failure(tmp_path):
    workdir = materialize_sample_repo(tmp_path / "textkit")
    # The runner adapter maps an empty claude result to success=False; the
    # orchestrator then surfaces a DemoEngineError rather than shipping.
    with pytest.raises(DemoEngineError):
        run_demo(
            engine=lambda call: EngineOutcome(success=False, text="", error_message="empty"),
            events=lambda e: None,
            approve=lambda _p: True,
            workdir=workdir,
            timeout=30,
        )


# ---------------------------------------------------------------------------
# Presenter
# ---------------------------------------------------------------------------


def test_presenter_non_interactive_auto_approves(tmp_path):
    out = tmp_path / "out.txt"
    with out.open("w") as handle:
        presenter = Presenter(stream=handle, color=False, interactive=False)
        assert presenter.approve("some plan") is True
    assert "auto-approved" in out.read_text()


def test_presenter_streams_events_without_color_when_not_tty(tmp_path):
    from demo.orchestrator import DemoEvent

    out = tmp_path / "out.txt"
    with out.open("w") as handle:
        presenter = Presenter(stream=handle, color=False, interactive=False)
        presenter.on_event(DemoEvent(step="plan", kind="start", text="drafting"))
        presenter.on_event(DemoEvent(step="plan", kind="done", text="a plan"))
    body = out.read_text()
    assert "[PLAN]" in body
    assert "\033[" not in body  # no ANSI codes on a non-tty stream


# ---------------------------------------------------------------------------
# Runner wiring
# ---------------------------------------------------------------------------


def test_runner_reports_missing_claude_cli(tmp_path, monkeypatch, capsys):
    runner = load_demo_runner()
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    code = runner.main([])
    assert code == 2
    assert "Claude Code CLI" in capsys.readouterr().out


def test_cli_demo_forwards_flags_to_runner(monkeypatch):
    cli = load_cli_module()
    calls: list[list[str]] = []

    def fake_run(command, check):
        calls.append(command)
        return _CompletedStub()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    args = _demo_namespace(keep=True, yes=True, timeout=45)
    assert cli.cmd_demo(args) == 0
    forwarded = calls[0]
    assert forwarded[-1] == "45"
    assert "--keep" in forwarded
    assert "--yes" in forwarded
    assert "--timeout" in forwarded
    assert str(ROOT / "bin/alfred-demo.py") in forwarded


class _CompletedStub:
    returncode = 0


def _demo_namespace(*, keep: bool, yes: bool, timeout: int):
    from types import SimpleNamespace

    return SimpleNamespace(keep=keep, yes=yes, timeout=timeout)

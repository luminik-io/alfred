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

    def __init__(
        self,
        *,
        catch_bug: bool = True,
        fail_step: str | None = None,
        noop_build: bool = False,
        break_tests: bool = False,
        omit_review_verdict: bool = False,
        broken_fix: bool = False,
    ) -> None:
        self.catch_bug = catch_bug
        self.fail_step = fail_step
        self.noop_build = noop_build
        self.break_tests = break_tests
        self.omit_review_verdict = omit_review_verdict
        self.broken_fix = broken_fix
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
            if not self.noop_build:
                self._append_slugify(call.workdir)
            if self.break_tests:
                self._append_failing_test(call.workdir)
            return EngineOutcome(success=True, text="[DEMO-BUILD-DONE] added slugify + tests")
        if call.step == "review":
            finding = (
                'titlecase splits on whitespace runs and rejoins with single spaces; "a  b" '
                '(two spaces) returns "A B" (one space), silently collapsing the input spacing.'
            )
            if self.omit_review_verdict:
                return EngineOutcome(success=True, text=finding)
            verdict = REVIEW_BLOCK_SENTINEL if self.catch_bug else REVIEW_PASS_SENTINEL
            return EngineOutcome(success=True, text=f"{finding}\n{verdict}")
        if call.step == "fix":
            if self.broken_fix:
                self._fix_titlecase_noop(call.workdir)
                return EngineOutcome(success=True, text="[DEMO-FIX-DONE] (did not really fix it)")
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
        """Genuinely repair the planted bug so titlecase preserves spacing.

        Mirrors what a real fix step must do: rewrite ``titlecase`` to keep the
        exact input whitespace (``"a  b"`` -> ``"A  B"``) instead of collapsing
        runs, and add a regression test. The ship step's post-fix gate
        re-runs the reviewer's reproduction, so an appended comment (which does
        not actually fix ``titlecase``) would now be rejected.
        """
        lib = workdir / "textkit.py"
        text = lib.read_text()
        text = text.replace(
            "    words = text.split()\n"
            '    return " ".join(word[:1].upper() + word[1:].lower() for word in words)',
            "    import re\n"
            "    return re.sub(\n"
            '        r"\\S+",\n'
            "        lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),\n"
            "        text,\n"
            "    )",
        )
        lib.write_text(text)
        tests = workdir / "test_textkit.py"
        tests.write_text(
            tests.read_text() + "\n\ndef test_titlecase_preserves_double_space() -> None:\n"
            '    assert textkit.titlecase("a  b") == "A  B"\n'
        )

    @staticmethod
    def _fix_titlecase_noop(workdir: Path) -> None:
        """A "fix" that edits a real file but leaves titlecase broken.

        Simulates a fix step that reports success and changes the worktree, but
        does not actually resolve the reported whitespace bug. The ship step's
        post-fix reproduction gate must catch this and refuse to ship.
        """
        lib = workdir / "textkit.py"
        lib.write_text(lib.read_text() + "\n# touched, but titlecase still collapses spaces\n")

    @staticmethod
    def _append_failing_test(workdir: Path) -> None:
        tests = workdir / "test_textkit.py"
        tests.write_text(
            tests.read_text() + "\n\ndef test_scripted_regression() -> None:\n    assert False\n"
        )


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


def test_planted_titlecase_bug_actually_manifests():
    """The review target must be a REAL defect, not a prompted hallucination.

    Guard against regressing the planted bug: ``titlecase`` in the bundled
    sample must genuinely collapse runs of consecutive whitespace, so the
    reviewer's repro (``titlecase("a  b")``) really shows the corruption.
    """
    loader = importlib.machinery.SourceFileLoader(
        "demo_sample_textkit", str(ROOT / "examples/demo-repo/textkit.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    sample = importlib.util.module_from_spec(spec)
    loader.exec_module(sample)

    # The bug: two spaces in, one space out. Leading whitespace is dropped too.
    assert sample.titlecase("a  b") == "A B"
    assert sample.titlecase("  a") == "A"
    # And the shipped tests still pass against it (single-space inputs).
    assert sample.titlecase("the quick brown fox") == "The Quick Brown Fox"


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
    # The ship step ran the sample test suite before committing.
    ship_details = [e.text for e in events if e.step == "ship" and e.kind == "detail"]
    assert any("test suite" in text for text in ship_details)
    assert any(text.startswith("Tests:") for text in ship_details)


def test_review_verdict_drives_fix_branch(tmp_path):
    engine = ScriptedEngine(catch_bug=False)
    result, events = _run(engine, tmp_path)

    assert result.shipped is True
    assert result.bug_caught is False
    # When the reviewer approves, no fix engine call is made.
    assert not any(c.step == "fix" for c in engine.calls)
    fix_done = [e for e in events if e.step == "fix" and e.kind == "done"]
    assert fix_done and "no fix was needed" in fix_done[0].text
    # The commit title must not claim a fix that never happened.
    assert "fix titlecase" not in result.diff_summary


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


def test_noop_successful_engine_never_ships(tmp_path):
    """Engine says success but edits nothing: ship must fail, never fake it.

    This is the Greptile P1 repro: a "successful" build that leaves the
    worktree untouched used to sail through ship and print the initial
    snapshot as a PR summary. Now it must raise at the ship step.
    """
    engine = ScriptedEngine(catch_bug=False, noop_build=True)
    with pytest.raises(DemoEngineError) as exc_info:
        _run(engine, tmp_path)
    assert exc_info.value.step == "ship"
    assert "unchanged" in exc_info.value.message


def test_review_without_verdict_token_is_a_failure(tmp_path):
    """Review prose with neither sentinel is not an implicit approval."""
    engine = ScriptedEngine(omit_review_verdict=True)
    with pytest.raises(DemoEngineError) as exc_info:
        _run(engine, tmp_path)
    assert exc_info.value.step == "review"
    assert "verdict" in exc_info.value.message
    # The run stopped at review: no fix call, no ship.
    assert [c.step for c in engine.calls] == ["plan", "build", "review"]


def test_ship_fails_when_sample_tests_fail(tmp_path):
    """A failing sample test suite blocks the ship step honestly."""
    engine = ScriptedEngine(catch_bug=False, break_tests=True)
    with pytest.raises(DemoEngineError) as exc_info:
        _run(engine, tmp_path)
    assert exc_info.value.step == "ship"
    assert "test suite failed" in exc_info.value.message
    # Nothing was committed over the broken state: HEAD is still the snapshot.
    workdir = tmp_path / "textkit"
    import subprocess

    head = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert head == "Initial textkit snapshot"


def test_ship_fails_when_fix_does_not_fix_the_planted_bug(tmp_path):
    """A fix that edits a file but leaves titlecase broken must not ship.

    The generic sample suite does not cover ``titlecase("a  b")``, so without
    the post-fix reproduction gate a no-op-on-titlecase fix would pass tests
    and ship under a "fix titlecase whitespace bug" summary. The gate re-runs
    the reviewer's exact reproduction and blocks.
    """
    engine = ScriptedEngine(catch_bug=True, broken_fix=True)
    with pytest.raises(DemoEngineError) as exc_info:
        _run(engine, tmp_path)
    assert exc_info.value.step == "ship"
    assert "did not resolve it" in exc_info.value.message
    # Nothing was committed: HEAD is still the initial snapshot.
    import subprocess

    head = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=str(tmp_path / "textkit"),
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert head == "Initial textkit snapshot"


def test_shipped_commit_excludes_test_cache_artifacts(tmp_path):
    """The shipped commit must never contain __pycache__/.pytest_cache/*.pyc.

    The sample suite runs before the commit and can create python test-cache
    artifacts in the worktree. Those must stay out of the shipped diff (the
    sample repo git-ignores them and the commit pathspec excludes them), so
    the PR-style diff only ever contains real source changes.
    """
    engine = ScriptedEngine(catch_bug=True)
    result, _events = _run(engine, tmp_path)
    assert result.shipped is True

    import subprocess

    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=str(tmp_path / "textkit"),
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    assert "__pycache__" not in tree
    assert ".pytest_cache" not in tree
    assert ".pyc" not in tree
    # The real source change is present.
    assert "textkit.py" in tree


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
    calls: list[tuple[list[str], int]] = []

    def fake_run(command, check, timeout):
        calls.append((command, timeout))
        return _CompletedStub()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    args = _demo_namespace(keep=True, yes=True, timeout=45)
    assert cli.cmd_demo(args) == 0
    forwarded, parent_timeout = calls[0]
    assert forwarded[-1] == "45"
    assert "--keep" in forwarded
    assert "--yes" in forwarded
    assert "--timeout" in forwarded
    assert str(ROOT / "bin/alfred-demo.py") in forwarded
    assert parent_timeout == cli._DELEGATED_COMMAND_TIMEOUT_S


def test_cli_demo_parent_timeout_scales_with_forwarded_step_limit(monkeypatch):
    cli = load_cli_module()
    timeouts: list[int] = []

    def fake_run(command, check, timeout):
        timeouts.append(timeout)
        return _CompletedStub()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.cmd_demo(_demo_namespace(keep=False, yes=True, timeout=300)) == 0
    assert timeouts == [(300 * 4) + 120]


class _CompletedStub:
    returncode = 0


def _demo_namespace(*, keep: bool, yes: bool, timeout: int):
    from types import SimpleNamespace

    return SimpleNamespace(keep=keep, yes=yes, timeout=timeout)

"""The senior-dev rubric grade-then-revise gate wiring (bin/senior-dev.py).

Covers the seam that wraps the BUILD step before a PR opens:

* OFF by default: with ``ALFRED_RUBRIC_GATE`` unset the gate returns ``None``
  and the build ships unchanged (no grader, no revision).
* Revision dispatch happens exactly ONCE on ``needs_revision`` when enabled,
  with the derived rubric coming from the issue's acceptance criteria, and the
  final verdict is returned for the PR body.
* The gate never blocks: whatever the final verdict, it returns a trajectory
  and the caller proceeds to open the PR.

The grader and the implementer re-dispatch are both stubbed, so no real LLM or
git runs here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parent.parent / "bin"
_LIB = Path(__file__).resolve().parent.parent / "lib"


@pytest.fixture()
def senior_dev(tmp_path, monkeypatch):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("GH_ORG", "myorg")
    monkeypatch.delenv("ALFRED_RUBRIC_GATE", raising=False)
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    spec = importlib.util.spec_from_file_location(
        "senior_dev_rubric_under_test", _BIN / "senior-dev.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, event_type: str, **payload):
        self.emitted.append((event_type, payload))


class _Spend:
    def __init__(self) -> None:
        self.increments: list[dict] = []

    def increment(self, **kwargs):
        self.increments.append(kwargs)


_ISSUE_WITH_CRITERIA = {
    "number": 12,
    "title": "Add widget",
    "body": (
        "Build the widget.\n\n"
        "## Acceptance criteria\n"
        "- [ ] Widget renders on the page\n"
        "- [ ] A test covers the render\n"
    ),
}


def test_gate_off_by_default_returns_none(senior_dev, monkeypatch):
    # ALFRED_RUBRIC_GATE unset: the whole gate is skipped, build ships as-is.
    called = {"grader": False}

    def _no_grader(**_kwargs):
        called["grader"] = True
        return lambda _p: "{}"

    monkeypatch.setattr(senior_dev, "build_rubric_grader", _no_grader)
    out = senior_dev._run_rubric_gate(
        "api",
        _ISSUE_WITH_CRITERIA,
        Path("/tmp/wt"),
        "HEAD~1",
        "feat/x",
        "fid",
        "codex",
        _Spend(),
        _Events(),
    )
    assert out is None
    assert called["grader"] is False  # never even built a grader


def test_gate_grades_then_revises_once_when_needs_revision(senior_dev, monkeypatch):
    monkeypatch.setenv("ALFRED_RUBRIC_GATE", "1")
    monkeypatch.setenv("ALFRED_RUBRIC_MAX_ITERATIONS", "1")

    # First grade -> needs_revision, regrade after one revision -> satisfied.
    grades = iter(
        [
            json.dumps(
                {
                    "result": "needs_revision",
                    "explanation": "missing test",
                    "criteria": [
                        {"name": "A test covers the render", "passed": False, "gap": "no test"}
                    ],
                }
            ),
            json.dumps(
                {
                    "result": "satisfied",
                    "explanation": "now covered",
                    "criteria": [{"name": "A test covers the render", "passed": True, "gap": None}],
                }
            ),
        ]
    )
    captured_rubric = {}

    def _grader(**_kwargs):
        return lambda _prompt: next(grades)

    monkeypatch.setattr(senior_dev, "build_rubric_grader", _grader)

    # Stub git: two diff reads (initial + post-revision), a clean worktree so no
    # salvage commit runs.
    diffs = iter(["diff v1 non-empty", "diff v2 non-empty"])

    def _fake_run(args, **_kw):
        if "status" in args:
            return type("R", (), {"stdout": "", "returncode": 0, "stderr": ""})()
        if "diff" in args:
            return type("R", (), {"stdout": next(diffs), "returncode": 0, "stderr": ""})()
        return type("R", (), {"stdout": "", "returncode": 0, "stderr": ""})()

    monkeypatch.setattr(senior_dev, "run", _fake_run)

    revise_calls = []

    def _fake_invoke(prompt, **_kwargs):
        revise_calls.append(prompt)
        return type("Res", (), {"num_turns": 3, "cost_usd": 0.01})(), "codex"

    monkeypatch.setattr(senior_dev, "invoke_agent_engine", _fake_invoke)

    # Capture the rubric the grader was asked to grade against.
    real_derive = senior_dev.derive_rubric

    def _spy_derive(body, **kw):
        r = real_derive(body, **kw)
        captured_rubric["rubric"] = r
        return r

    monkeypatch.setattr(senior_dev, "derive_rubric", _spy_derive)

    events = _Events()
    spend = _Spend()
    verdicts = senior_dev._run_rubric_gate(
        "api",
        _ISSUE_WITH_CRITERIA,
        Path("/tmp/wt"),
        "HEAD~1",
        "feat/x",
        "fid",
        "codex",
        spend,
        events,
    )

    assert verdicts is not None
    assert [v.result for v in verdicts] == ["needs_revision", "satisfied"]
    assert len(revise_calls) == 1  # implementer re-dispatched exactly once
    assert "no test" in revise_calls[0]  # the gap threaded into the revision prompt
    # Rubric was derived from the issue's acceptance criteria, not the generic set.
    assert captured_rubric["rubric"] == [
        "Widget renders on the page",
        "A test covers the render",
    ]
    # The revision's model spend was recorded.
    assert any("turns_today" in inc for inc in spend.increments)
    # An observability event carries the final result.
    graded = [p for t, p in events.emitted if t == "rubric_graded"]
    assert graded and graded[0]["result"] == "satisfied"
    assert graded[0]["revisions"] == 1


def test_gate_empty_diff_skips_grading(senior_dev, monkeypatch):
    monkeypatch.setenv("ALFRED_RUBRIC_GATE", "1")
    monkeypatch.setattr(senior_dev, "run", lambda *a, **k: type("R", (), {"stdout": "   \n"})())

    def _no_grader(**_kwargs):
        raise AssertionError("grader must not run on an empty diff")

    monkeypatch.setattr(senior_dev, "build_rubric_grader", _no_grader)
    out = senior_dev._run_rubric_gate(
        "api",
        _ISSUE_WITH_CRITERIA,
        Path("/tmp/wt"),
        "HEAD~1",
        "feat/x",
        "fid",
        "codex",
        _Spend(),
        _Events(),
    )
    assert out is None


def test_revision_leaving_uncommitted_edits_is_salvaged_and_committed(senior_dev, monkeypatch):
    # A revision run that edits but does not commit must not leave the tree dirty
    # or hide its edits from the graded committed diff: the gate salvages them
    # into a commit under the revision-scoped trailer.
    monkeypatch.setenv("ALFRED_RUBRIC_GATE", "1")
    monkeypatch.setenv("ALFRED_RUBRIC_MAX_ITERATIONS", "1")

    grades = iter(
        [
            json.dumps(
                {
                    "result": "needs_revision",
                    "explanation": "gap",
                    "criteria": [{"name": "c", "passed": False, "gap": "fix it"}],
                }
            ),
            json.dumps(
                {
                    "result": "satisfied",
                    "explanation": "ok",
                    "criteria": [{"name": "c", "passed": True, "gap": None}],
                }
            ),
        ]
    )
    monkeypatch.setattr(senior_dev, "build_rubric_grader", lambda **_k: lambda _p: next(grades))
    monkeypatch.setattr(
        senior_dev,
        "invoke_agent_engine",
        lambda *a, **k: (type("R", (), {"num_turns": 1, "cost_usd": 0.0})(), "codex"),
    )

    calls: list[list[str]] = []
    diffs = iter(["diff before", "diff after salvage"])
    # First status (in _revise) reports a DIRTY tree; the add + commit then run.
    status_results = iter([" M lib/foo.py"])  # dirty once, clean thereafter

    def _fake_run(args, **_kw):
        calls.append(list(args))
        if "status" in args:
            return type(
                "R", (), {"stdout": next(status_results, ""), "returncode": 0, "stderr": ""}
            )()
        if "diff" in args:
            return type("R", (), {"stdout": next(diffs), "returncode": 0, "stderr": ""})()
        # git add / git commit
        return type("R", (), {"stdout": "", "returncode": 0, "stderr": ""})()

    monkeypatch.setattr(senior_dev, "run", _fake_run)

    verdicts = senior_dev._run_rubric_gate(
        "api",
        _ISSUE_WITH_CRITERIA,
        Path("/tmp/wt"),
        "HEAD~1",
        "feat/x",
        "fid",
        "codex",
        _Spend(),
        _Events(),
    )
    assert [v.result for v in verdicts] == ["needs_revision", "satisfied"]
    # The salvage staged and committed the uncommitted revision edits.
    assert any(a[:2] == ["git", "add"] for a in calls)
    commit_calls = [a for a in calls if a[:2] == ["git", "commit"]]
    assert commit_calls, "uncommitted revision edits must be committed"
    # The salvage commit carries the revision-scoped firing id, not the plain one.
    assert "fid-revise" in " ".join(commit_calls[0])

"""Tests for lib/memory_benchmark.py and the `alfred benchmark memory` CLI.

The harness is offline: the scoring core never calls an LLM, and the A/B is run
with a deterministic stub solver over the built-in fixture plus a real in-memory
FleetBrain (SQLite ``:memory:``). Nothing here touches the network, the real
disk outside ``tmp_path``, or a model, and no quota is burned. Only the
real-engine solver (``make_cli_engine_solver``) is left uncovered, by design:
exercising it needs a live model.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import memory_benchmark as mb  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "mem-bench"


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def test_load_builtin_fixture():
    fixture = mb.load_fixture(FIXTURE_DIR)
    assert len(fixture.tasks) == 5
    assert len(fixture.lessons) == 7
    ids = {t.task_id for t in fixture.tasks}
    assert "tz-naive-datetime" in ids
    # Exactly the four known-mistake tasks are eligible; the docstring task is a
    # control that must not inflate N.
    eligible = [t for t in fixture.tasks if t.repeats_known_mistake]
    assert len(eligible) == 4
    assert {t.task_id for t in fixture.tasks if not t.repeats_known_mistake} == {"add-docstring"}


def test_load_fixture_missing_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        mb.load_fixture(tmp_path / "nope")


def test_load_fixture_tolerates_garbage(tmp_path: Path):
    (tmp_path / "tasks.json").write_text("{not json")
    (tmp_path / "lessons.json").write_text(
        json.dumps([{"no_id": 1}, {"lesson_id": "x", "body": "b"}])
    )
    fixture = mb.load_fixture(tmp_path)
    assert fixture.tasks == ()  # garbage tasks skipped
    assert [le.lesson_id for le in fixture.lessons] == ["x"]  # entry without id dropped


def test_default_fixture_dir_points_at_builtin():
    assert mb.default_fixture_dir() == FIXTURE_DIR


# --------------------------------------------------------------------------
# judge_solution (deterministic marker match)
# --------------------------------------------------------------------------


def _task(**kw) -> mb.MemTask:
    base = {
        "task_id": "t",
        "kind": "fix",
        "title": "T",
        "prompt": "do it",
        "mistake_id": "m",
    }
    base.update(kw)
    return mb.MemTask(**base)


def test_judge_detects_mistake():
    task = _task(mistake_markers=(r"datetime\.now\(\)",), success_markers=(r"UTC",))
    made, ok = mb.judge_solution(task, "return datetime.now()")
    assert made is True
    assert ok is False


def test_judge_success_requires_success_marker_and_no_mistake():
    task = _task(
        mistake_markers=(r"datetime\.now\(\)",), success_markers=(r"datetime\.now\(UTC\)",)
    )
    made, ok = mb.judge_solution(task, "return datetime.now(UTC)")
    assert made is False
    assert ok is True


def test_judge_mistake_beats_success_marker():
    # A solution that both fixes intent AND repeats the mistake is not a success.
    task = _task(mistake_markers=(r"=\[\]",), success_markers=(r"is None",))
    made, ok = mb.judge_solution(task, "def f(x=[]):\n    if x is None: pass")
    assert made is True
    assert ok is False


def test_judge_malformed_marker_falls_back_to_substring():
    task = _task(mistake_markers=("(unclosed",))
    made, _ = mb.judge_solution(task, "this has (unclosed in it")
    assert made is True


# --------------------------------------------------------------------------
# Real recall + injection (in-memory FleetBrain, no model)
# --------------------------------------------------------------------------


def test_seed_provider_recalls_relevant_lesson_first():
    fixture = mb.load_fixture(FIXTURE_DIR)
    provider = mb.seed_fleet_provider(fixture.lessons, codename=fixture.codename, repo=fixture.repo)
    # The literal-match path surfaces the timezone lesson for the tz query.
    lessons = provider.recall(
        query="timezone", codename=fixture.codename, repo=fixture.repo, limit=3
    )
    assert lessons[0].id == "L-tz"


def test_default_recall_and_inject_use_recall_query():
    fixture = mb.load_fixture(FIXTURE_DIR)
    provider = mb.seed_fleet_provider(fixture.lessons, codename=fixture.codename, repo=fixture.repo)
    task = next(t for t in fixture.tasks if t.task_id == "swallow-exceptions")
    lessons = mb.default_recall_fn(provider, task, fixture.codename, fixture.repo, 3)
    assert "L-exc" in {le.id for le in lessons}
    context = mb.default_inject_fn(provider, task, fixture.codename, fixture.repo, 3)
    assert task.lesson_signal.lower() in context.lower()


# --------------------------------------------------------------------------
# Metric maths
# --------------------------------------------------------------------------


def _attempt(task_id, arm, *, mistake, ok, recalled=(), turns=5, tin=1000) -> mb.TaskAttempt:
    return mb.TaskAttempt(
        task_id=task_id,
        arm=arm,
        made_mistake=mistake,
        succeeded=ok,
        recalled_lesson_ids=tuple(recalled),
        turns=turns,
        tokens=mb.TokenUsage(tokens_in=tin, tokens_out=200),
    )


def test_arm_metrics_denominators():
    suite = (
        _task(task_id="a", repeats_known_mistake=True),
        _task(task_id="b", repeats_known_mistake=True),
        _task(task_id="c", repeats_known_mistake=False),
    )
    attempts = [
        _attempt("a", "memory_off", mistake=True, ok=False),
        _attempt("b", "memory_off", mistake=False, ok=True),
        _attempt("c", "memory_off", mistake=False, ok=True),
    ]
    m = mb.build_arm_metrics("memory_off", attempts, suite)
    assert m.mistake_eligible == 2  # only a,b are known-mistake tasks
    assert m.mistakes_repeated == 1
    assert m.repeated_mistake_rate == pytest.approx(0.5)
    assert m.succeeded == 2
    assert m.task_success_rate == pytest.approx(2 / 3)
    assert m.turns == 15
    assert m.turns_per_task == pytest.approx(5.0)


def test_arm_metrics_empty_is_honest_none():
    m = mb.build_arm_metrics("memory_off", [], ())
    assert m.repeated_mistake_rate is None
    assert m.task_success_rate is None
    assert m.turns_per_task is None
    assert m.retrieval.recall is None
    assert m.retrieval.precision is None


def test_retrieval_metrics_precision_recall():
    suite = (
        _task(task_id="a", relevant_lesson_ids=("L1",)),
        _task(task_id="b", relevant_lesson_ids=("L2",)),
        _task(task_id="c", relevant_lesson_ids=()),  # control: excluded from retrieval
    )
    attempts = [
        _attempt("a", "memory_on", mistake=False, ok=True, recalled=("L1", "D1", "D2")),
        _attempt("b", "memory_on", mistake=False, ok=True, recalled=("L2", "D1", "D3")),
        _attempt("c", "memory_on", mistake=False, ok=True, recalled=("D1", "D2", "D3")),
    ]
    m = mb.build_arm_metrics("memory_on", attempts, suite)
    r = m.retrieval
    assert r.tasks_with_relevant == 2
    assert r.relevant_total == 2
    assert r.recalled_relevant == 2
    assert r.recalled_total == 6  # 3 + 3 over the two tasks with a relevant lesson
    assert r.recall == pytest.approx(1.0)
    assert r.precision == pytest.approx(2 / 6, abs=1e-3)


def test_retrieval_recall_zero_when_nothing_recalled():
    suite = (_task(task_id="a", relevant_lesson_ids=("L1",)),)
    attempts = [_attempt("a", "memory_off", mistake=True, ok=False, recalled=())]
    m = mb.build_arm_metrics("memory_off", attempts, suite)
    # relevant existed but nothing recalled -> recall 0.0, precision None (no set).
    assert m.retrieval.recall == pytest.approx(0.0)
    assert m.retrieval.precision is None


# --------------------------------------------------------------------------
# End-to-end A/B with the stub solver (real recall/inject, mocked engine)
# --------------------------------------------------------------------------


def test_stub_ab_headline_memory_prevents_repeats():
    fixture = mb.load_fixture(FIXTURE_DIR)
    report = mb.run_memory_ab(fixture, solver=mb.make_stub_solver(), label="test")

    # N is the count of known-mistake tasks, reported on both arms.
    assert report.memory_off.mistake_eligible == 4
    assert report.memory_on.mistake_eligible == 4

    # Headline: memory OFF repeats every known mistake; memory ON repeats none.
    assert report.memory_off.repeated_mistake_rate == pytest.approx(1.0)
    assert report.memory_on.repeated_mistake_rate == pytest.approx(0.0)
    assert report.repeated_mistake_rate_delta == pytest.approx(1.0)

    # Task success follows: the control succeeds either way, the four
    # mistake tasks only succeed when memory recalled the lesson.
    assert report.memory_off.task_success_rate == pytest.approx(0.2)
    assert report.memory_on.task_success_rate == pytest.approx(1.0)
    assert report.success_rate_delta == pytest.approx(0.8)


def test_real_engine_solver_isolates_every_attempt(tmp_path: Path, monkeypatch):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "fixture.py").write_text("ORIGINAL\n", encoding="utf-8")
    seen: list[Path] = []

    def fake_run(command, *, cwd, **kwargs):
        attempt = Path(cwd)
        seen.append(attempt)
        assert attempt != source
        assert (attempt / "fixture.py").read_text(encoding="utf-8") == "ORIGINAL\n"
        assert not (attempt / "prior-attempt.txt").exists()
        (attempt / "prior-attempt.txt").write_text("mutated", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(mb.subprocess, "run", fake_run)
    solver = mb.make_cli_engine_solver(cwd=source)
    task = _task()

    solver(task, "lesson", mb.ARM_ON)
    solver(task, "", mb.ARM_OFF)

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert not (source / "prior-attempt.txt").exists()
    assert all(not path.exists() for path in seen)


def test_stub_ab_retrieval_and_off_arm_recalls_nothing():
    fixture = mb.load_fixture(FIXTURE_DIR)
    report = mb.run_memory_ab(fixture, solver=mb.make_stub_solver())

    # memory ON: the right lesson is always recalled (recall 1.0); distractors
    # in the top-3 keep precision below 1 (4 relevant of 12 recalled).
    assert report.memory_on.retrieval.recall == pytest.approx(1.0)
    assert report.memory_on.retrieval.recalled_relevant == 4
    assert report.memory_on.retrieval.recalled_total == 12
    assert report.memory_on.retrieval.precision == pytest.approx(4 / 12, abs=1e-3)

    # memory OFF: a true no-memory control - nothing recalled at all.
    off_attempts = [a for a in report.attempts if a.arm == "memory_off"]
    assert all(a.recalled_lesson_ids == () for a in off_attempts)
    assert report.memory_off.retrieval.recall == pytest.approx(0.0)
    assert report.memory_off.retrieval.precision is None


def test_report_to_dict_shape():
    fixture = mb.load_fixture(FIXTURE_DIR)
    report = mb.run_memory_ab(fixture, solver=mb.make_stub_solver())
    payload = report.to_dict()
    assert payload["memory_off"]["repeated_mistake_rate"] == pytest.approx(1.0)
    assert payload["delta"]["repeated_mistake_rate"] == pytest.approx(1.0)
    assert len(payload["attempts"]) == 10  # 5 tasks x 2 arms
    assert payload["solver_kind"] == "stub"


def test_benchmark_module_reexports_memory_ab():
    # The memory A/B surface is reachable from lib/benchmark.py (lazy re-export).
    import benchmark

    assert benchmark.run_memory_ab is mb.run_memory_ab
    with pytest.raises(AttributeError):
        _ = benchmark.does_not_exist


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "alfred_benchmark_cli", str(REPO_ROOT / "bin" / "alfred-benchmark.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_memory_stub_table(capsys):
    cli = _load_cli()
    rc = cli.main(["memory", "--stub"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "repeated-mistake-rate" in out
    assert "N=4" in out
    assert "ILLUSTRATIVE" in out
    assert "memory OFF" in out and "memory ON" in out


def test_cli_prioritizes_checkout_lib_over_deployed_runtime(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    (runtime / "lib").mkdir(parents=True)
    monkeypatch.setenv("ALFRED_HOME", str(runtime))

    _load_cli()

    assert sys.path[0] == str(REPO_ROOT / "lib")


def test_cli_memory_stub_json(capsys):
    cli = _load_cli()
    rc = cli.main(["memory", "--stub", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["memory_on"]["repeated_mistake_rate"] == 0.0
    assert payload["memory_off"]["repeated_mistake_rate"] == 1.0


def test_cli_memory_show_suite(capsys):
    cli = _load_cli()
    rc = cli.main(["memory", "--show-suite"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tz-naive-datetime" in out
    assert "control" in out  # the docstring task is flagged as a control


def test_cli_memory_requires_a_solver(capsys):
    cli = _load_cli()
    rc = cli.main(["memory"])
    assert rc == 1
    assert "pick a solver" in capsys.readouterr().err


def test_cli_memory_rejects_both_solvers(capsys):
    cli = _load_cli()
    rc = cli.main(["memory", "--stub", "--engine", "claude"])
    assert rc == 1
    assert "not both" in capsys.readouterr().err


def test_cli_memory_missing_fixture_exit_2(tmp_path: Path, capsys):
    cli = _load_cli()
    rc = cli.main(["memory", "--stub", "--fixture", str(tmp_path / "missing")])
    assert rc == 2
    assert "fixture" in capsys.readouterr().err.lower()

"""Tests for lib/memory_benchmark.py and the `alfred benchmark memory` CLI.

The harness is offline: the scoring core never calls an LLM, and the A/B is run
with a deterministic stub solver over the built-in fixture plus a real in-memory
FleetBrain (SQLite ``:memory:``). Nothing here touches the network, the real
disk outside ``tmp_path``, or a model, and no quota is burned. Only the
real-engine solver (``make_cli_engine_solver``) is left uncovered, by design:
exercising it needs a live model.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
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
# Real recall + injection (shipped in-memory provider chain, no model)
# --------------------------------------------------------------------------


def test_seed_default_provider_matches_shipped_chain():
    fixture = mb.load_fixture(FIXTURE_DIR)
    provider = mb.seed_default_provider(
        fixture.lessons,
        codename=fixture.codename,
        repo=fixture.repo,
    )

    assert provider.name == "chained"
    assert [member.name for member in provider.providers] == ["sqlite", "fleet"]


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
# Provider recall-quality benchmark
# --------------------------------------------------------------------------


def test_recall_quality_measures_false_injection_misses_latency_and_prompt_bytes():
    cases = (
        mb.RecallQualityCase(
            case_id="exact",
            category="exact",
            query="GraphQL schema",
            repo="acme/api",
            relevant_lesson_ids=("L-schema",),
        ),
        mb.RecallQualityCase(
            case_id="wording-variant",
            category="wording-variant",
            query="keep database lookups together",
            repo="acme/api",
            relevant_lesson_ids=("L-batch",),
        ),
        mb.RecallQualityCase(
            case_id="miss",
            category="empty-miss",
            query="change the company logo",
            repo="acme/api",
        ),
    )

    class FakeProvider:
        name = "fake"

        def recall(self, *, query=None, codename=None, repo=None, limit=3):
            lesson_ids = {
                "GraphQL schema": ("L-schema", "D-style"),
                "keep database lookups together": ("L-batch",),
                "change the company logo": ("D-style",),
            }[query]
            return [type("Lesson", (), {"id": lesson_id})() for lesson_id in lesson_ids]

    clock_values = iter((1.000, 1.004, 2.000, 2.006, 3.000, 3.010))
    report = mb.run_recall_quality(
        cases,
        provider=FakeProvider(),
        codename="mem-bench",
        limit=3,
        context_fn=lambda _provider, case, _codename, _limit: {
            "exact": "schema lesson",
            "wording-variant": "batch lesson",
            "miss": "wrong lesson",
        }[case.case_id],
        clock=lambda: next(clock_values),
    )

    assert report.provider == "fake"
    assert report.model == "none"
    assert report.network is False
    assert report.limitations == (
        "fixed-fixture-provider-recall",
        "no-model-reasoning",
        "no-live-operator-data",
    )
    assert report.metrics.cases == 3
    assert report.metrics.cases_with_relevant == 2
    assert report.metrics.relevant_total == 2
    assert report.metrics.recalled_total == 4
    assert report.metrics.recalled_relevant == 2
    assert report.metrics.precision == pytest.approx(0.5)
    assert report.metrics.recall == pytest.approx(1.0)
    assert report.metrics.false_injections == 2
    assert report.metrics.false_injection_rate == pytest.approx(0.5)
    assert report.metrics.empty_miss_cases == 1
    assert report.metrics.nonempty_misses == 1
    assert report.metrics.empty_miss_rate == pytest.approx(0.0)
    assert report.metrics.mean_latency_ms == pytest.approx(20 / 3)
    assert report.metrics.prompt_bytes == len("schema lessonbatch lessonwrong lesson")
    assert [result.case_id for result in report.results] == [
        "exact",
        "wording-variant",
        "miss",
    ]


def test_recall_quality_reports_an_empty_result_when_provider_lookup_fails():
    case = mb.RecallQualityCase(
        case_id="provider-failure",
        category="exact",
        query="GraphQL schema",
        repo="acme/api",
        relevant_lesson_ids=("L-schema",),
    )

    class FailingProvider:
        name = "failing"

        def recall(self, **_kwargs):
            raise RuntimeError("offline")

    report = mb.run_recall_quality(
        (case,),
        provider=FailingProvider(),
        codename="mem-bench",
        context_fn=lambda *_args: "",
        clock=iter((1.0, 1.0)).__next__,
    )

    assert report.results[0].recalled_lesson_ids == ()
    assert report.results[0].error == "provider_lookup_failed"
    assert report.metrics.recall == pytest.approx(0.0)
    assert report.metrics.precision is None


def test_recall_quality_keeps_recall_metrics_when_context_formatting_fails():
    case = mb.RecallQualityCase(
        case_id="context-failure",
        category="exact",
        query="GraphQL schema",
        repo="acme/api",
        relevant_lesson_ids=("L-schema",),
    )

    class Provider:
        name = "fake"

        def recall(self, **_kwargs):
            return [type("Lesson", (), {"id": "L-schema"})()]

    def fail_context(*_args):
        raise RuntimeError("formatter offline")

    report = mb.run_recall_quality(
        (case,),
        provider=Provider(),
        context_fn=fail_context,
        clock=iter((1.0, 1.0)).__next__,
    )

    assert report.results[0].recalled_lesson_ids == ("L-schema",)
    assert report.results[0].prompt_bytes == 0
    assert report.results[0].error == "context_format_failed"
    assert report.metrics.recall == pytest.approx(1.0)


def test_recall_quality_formats_the_scored_recall_without_a_second_lookup():
    from memory import Lesson

    case = mb.RecallQualityCase(
        case_id="one-lookup",
        category="exact",
        query="GraphQL schema",
        repo="acme/api",
        relevant_lesson_ids=("L-schema",),
    )

    class Provider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def recall(self, **_kwargs):
            self.calls += 1
            return [
                Lesson(
                    id="L-schema",
                    codename="mem-bench",
                    repo="acme/api",
                    body="Run GraphQL schema validation before deployment.",
                    tags=[],
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    firing_id=None,
                )
            ]

    provider = Provider()
    report = mb.run_recall_quality((case,), provider=provider)

    assert provider.calls == 1
    assert report.results[0].prompt_bytes > 0


def test_recall_quality_measures_index_and_one_body_hydration_query():
    lessons = (
        mb.RecallQualitySeedLesson(
            lesson_id="L-one",
            body="GraphQL schema checks run before deployment.",
            repo="acme/api",
            tags=("graphql", "schema"),
        ),
        mb.RecallQualitySeedLesson(
            lesson_id="L-two",
            body="GraphQL schema changes require compatibility tests.",
            repo="acme/api",
            tags=("graphql", "schema"),
        ),
        mb.RecallQualitySeedLesson(
            lesson_id="L-other",
            body="Redis cache keys include the repository name.",
            repo="acme/api",
            tags=("redis", "cache"),
        ),
    )
    case = mb.RecallQualityCase(
        case_id="two-results",
        category="exact",
        query="GraphQL schema",
        repo="acme/api",
        relevant_lesson_ids=("L-one", "L-two"),
    )
    provider = mb.seed_recall_quality_provider(lessons)

    report = mb.run_recall_quality(
        (case,),
        provider=provider,
        context_fn=lambda *_args: "",
    )

    index = report.index
    assert index is not None
    assert index.corpus_lessons == 3
    assert index.corpus_body_bytes == sum(len(item.body.encode("utf-8")) for item in lessons)
    assert index.searchable_text_bytes >= index.corpus_body_bytes
    assert index.index_queries >= 1
    assert index.hydration_queries == 1
    assert index.hydrated_lessons == 2
    assert index.hydrated_body_bytes == sum(len(item.body.encode("utf-8")) for item in lessons[:2])
    assert index.full_scan_body_bytes == index.corpus_body_bytes
    assert index.avoided_body_bytes == index.corpus_body_bytes - index.hydrated_body_bytes
    assert index.avoided_body_rate == pytest.approx(
        index.avoided_body_bytes / index.full_scan_body_bytes
    )


def test_recall_quality_does_not_invent_index_metrics_for_an_unknown_provider():
    class Provider:
        name = "unknown"

        def recall(self, **_kwargs):
            return []

    report = mb.run_recall_quality((), provider=Provider())

    assert report.index is None


def test_recall_quality_keeps_index_metrics_when_the_same_chain_is_reused():
    lesson = mb.RecallQualitySeedLesson(
        lesson_id="L-schema",
        body="Run GraphQL schema validation before deployment.",
        repo="acme/api",
    )
    case = mb.RecallQualityCase(
        case_id="exact",
        category="exact",
        query="GraphQL schema validation",
        repo="acme/api",
        relevant_lesson_ids=(lesson.lesson_id,),
    )
    provider = mb.seed_recall_quality_provider((lesson,))

    first = mb.run_recall_quality((case,), provider=provider, context_fn=lambda *_args: "")
    second = mb.run_recall_quality((case,), provider=provider, context_fn=lambda *_args: "")

    assert first.index is not None
    assert second.index is not None
    assert second.index.corpus_lessons == 1
    assert second.index.hydration_queries == 1


def test_recall_quality_uses_and_records_an_isolated_prompt_budget(monkeypatch):
    from memory import Lesson

    monkeypatch.setenv("ALFRED_MEMORY_INJECT_MAX_CHARS", "1")
    case = mb.RecallQualityCase(
        case_id="isolated-budget",
        category="exact",
        query="GraphQL schema",
        repo="acme/api",
        relevant_lesson_ids=("L-schema",),
    )

    class Provider:
        name = "fake"

        def recall(self, **_kwargs):
            return [
                Lesson(
                    id="L-schema",
                    codename="mem-bench",
                    repo="acme/api",
                    body="Run GraphQL schema validation before deployment.",
                    tags=[],
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    firing_id=None,
                )
            ]

    report = mb.run_recall_quality((case,), provider=Provider())

    assert report.prompt_max_chars == 8000
    assert report.results[0].prompt_bytes > 0


def test_recall_quality_reports_failures_swallowed_by_a_provider_chain():
    from memory.providers import ChainedMemoryProvider

    case = mb.RecallQualityCase(
        case_id="member-failure",
        category="empty-miss",
        query="company logo typeface",
        repo="acme/api",
    )

    class FailingProvider:
        name = "broken"

        def recall(self, **_kwargs):
            raise RuntimeError("offline")

    class EmptyProvider:
        name = "empty"

        def recall(self, **_kwargs):
            return []

    report = mb.run_recall_quality(
        (case,),
        provider=ChainedMemoryProvider([FailingProvider(), EmptyProvider()]),
        context_fn=lambda *_args: "",
    )

    assert report.results[0].recalled_lesson_ids == ()
    assert report.results[0].error == "provider_member_failed:broken"


def test_recall_quality_fixture_digest_is_stable_and_content_bound():
    fixture = mb.load_recall_quality_fixture(mb.default_recall_quality_fixture_dir())
    digest = mb.recall_quality_fixture_digest(fixture)

    assert len(digest) == 64
    assert mb.recall_quality_fixture_digest(fixture) == digest
    changed = mb.RecallQualityFixture(
        cases=fixture.cases,
        lessons=(*fixture.lessons[:-1], dataclasses.replace(fixture.lessons[-1], body="changed")),
        codename=fixture.codename,
    )
    assert mb.recall_quality_fixture_digest(changed) != digest


def test_recall_quality_empty_suite_has_explicit_undefined_rates():
    provider = type("Provider", (), {"name": "fake"})()
    report = mb.run_recall_quality((), provider=provider)

    assert report.results == ()
    assert report.metrics.cases == 0
    assert report.metrics.prompt_bytes == 0
    assert report.metrics.precision is None
    assert report.metrics.recall is None
    assert report.metrics.false_injection_rate is None
    assert report.metrics.empty_miss_rate is None
    assert report.metrics.mean_latency_ms is None
    assert report.to_dict()["results"] == []


def test_builtin_recall_quality_fixture_covers_v080_risk_categories():
    fixture_dir = mb.default_recall_quality_fixture_dir()
    fixture = mb.load_recall_quality_fixture(fixture_dir)

    assert fixture_dir == REPO_ROOT / "tests" / "fixtures" / "memory-recall-quality"
    assert {case.category for case in fixture.cases} == {
        "exact",
        "wording-variant",
        "repo-scope",
        "temporal-update",
        "contradiction",
        "expired",
        "empty-miss",
    }
    assert len(fixture.lessons) >= 8


def test_recall_quality_fixture_rejects_missing_lesson_references(tmp_path):
    (tmp_path / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "broken-reference",
                    "category": "exact",
                    "query": "GraphQL schema",
                    "repo": "acme/api",
                    "relevant_lesson_ids": ["L-missing"],
                }
            ]
        )
    )
    (tmp_path / "lessons.json").write_text(
        json.dumps(
            [
                {
                    "lesson_id": "L-present",
                    "body": "Run GraphQL schema validation.",
                    "repo": "acme/api",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="unknown lesson L-missing"):
        mb.load_recall_quality_fixture(tmp_path)


def test_recall_quality_fixture_rejects_malformed_case_data(tmp_path):
    (tmp_path / "cases.json").write_text("not-json")
    (tmp_path / "lessons.json").write_text("[]")

    with pytest.raises(ValueError, match="invalid recall fixture JSON"):
        mb.load_recall_quality_fixture(tmp_path)


def test_recall_quality_fixture_rejects_invalid_timestamps(tmp_path):
    (tmp_path / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "expiry",
                    "category": "expired",
                    "query": "cache warmup",
                    "repo": "acme/api",
                    "relevant_lesson_ids": [],
                }
            ]
        )
    )
    (tmp_path / "lessons.json").write_text(
        json.dumps(
            [
                {
                    "lesson_id": "L-expired",
                    "body": "Use the expired cache warmup.",
                    "repo": "acme/api",
                    "valid_until": "not-a-time",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="invalid recall fixture timestamp"):
        mb.load_recall_quality_fixture(tmp_path)


def test_recall_quality_fixture_requires_complete_supersession_pairs(tmp_path):
    (tmp_path / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "expiry",
                    "category": "expired",
                    "query": "cache warmup",
                    "repo": "acme/api",
                    "relevant_lesson_ids": [],
                }
            ]
        )
    )
    (tmp_path / "lessons.json").write_text(
        json.dumps(
            [
                {
                    "lesson_id": "L-old",
                    "body": "Use the old cache warmup.",
                    "repo": "acme/api",
                    "superseded_by": "L-new",
                },
                {
                    "lesson_id": "L-new",
                    "body": "Cache population is automatic.",
                    "repo": "acme/api",
                },
            ]
        )
    )

    with pytest.raises(ValueError, match="valid_until with superseded_by"):
        mb.load_recall_quality_fixture(tmp_path)


def test_builtin_recall_quality_fixture_runs_against_the_shipped_chain():
    fixture = mb.load_recall_quality_fixture(mb.default_recall_quality_fixture_dir())
    provider = mb.seed_recall_quality_provider(fixture.lessons)
    report = mb.run_recall_quality(
        fixture.cases,
        provider=provider,
        codename=fixture.codename,
        context_fn=lambda *_args: "",
    )

    by_id = {result.case_id: result for result in report.results}
    assert by_id["repo-scope"].recalled_lesson_ids == ("L-api-scope",)
    assert "L-old-timeout" not in by_id["temporal-update"].recalled_lesson_ids
    assert by_id["temporal-update"].recalled_lesson_ids == ("L-new-timeout",)
    assert "L-old-retry" not in by_id["contradiction"].recalled_lesson_ids
    assert by_id["contradiction"].recalled_lesson_ids == ("L-new-retry",)
    assert by_id["expired"].recalled_lesson_ids == ()
    assert by_id["empty-miss"].recalled_lesson_ids == ()
    assert report.metrics.false_injections == 0
    assert report.metrics.empty_miss_rate == pytest.approx(1.0)


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

    # The shipped SQLite-first chain recalls only the relevant lesson for each
    # eligible task in the built-in fixture.
    assert report.memory_on.retrieval.recall == pytest.approx(1.0)
    assert report.memory_on.retrieval.recalled_relevant == 4
    assert report.memory_on.retrieval.recalled_total == 4
    assert report.memory_on.retrieval.precision == pytest.approx(1.0)

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
    assert payload["memory_provider"] == "sqlite,fleet"


def test_benchmark_module_reexports_memory_ab():
    # The memory A/B surface is reachable from lib/benchmark.py (lazy re-export).
    import benchmark

    assert benchmark.run_memory_ab is mb.run_memory_ab
    assert benchmark.run_recall_quality is mb.run_recall_quality
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
    assert "memory: sqlite,fleet" in out


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


def test_cli_memory_recall_quality_table(capsys):
    cli = _load_cli()
    rc = cli.main(["memory-recall"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "memory recall quality" in out
    assert "false injection rate" in out
    assert "empty miss rate" in out
    assert "temporal-update" in out
    assert "memory: sqlite,fleet" in out
    assert "body bytes avoided" in out


def test_cli_memory_recall_quality_json(capsys):
    cli = _load_cli()
    rc = cli.main(["memory-recall", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "sqlite,fleet"
    assert payload["fixture"] == "memory-recall-quality"
    assert payload["fixture_schema_version"] == 1
    assert len(payload["fixture_digest"]) == 64
    assert payload["prompt_max_chars"] == 8000
    assert payload["model"] == "none"
    assert payload["network"] is False
    assert payload["limitations"] == [
        "fixed-fixture-provider-recall",
        "no-model-reasoning",
        "no-live-operator-data",
    ]
    assert payload["metrics"]["cases"] == 7
    assert payload["metrics"]["false_injections"] == 0
    assert payload["index"]["corpus_lessons"] > 0
    assert payload["index"]["searchable_text_bytes"] > 0
    assert payload["index"]["hydration_queries"] > 0
    assert payload["index"]["hydrated_body_bytes"] > 0
    assert payload["index"]["avoided_body_bytes"] > 0
    assert len(payload["results"]) == 7


def test_cli_memory_recall_quality_fails_when_a_case_has_infrastructure_error(monkeypatch, capsys):
    cli = _load_cli()
    original = cli.run_recall_quality

    def failed_run(*args, **kwargs):
        report = original(*args, **kwargs)
        failed = dataclasses.replace(
            report.results[0],
            error="provider_member_failed:sqlite",
        )
        return dataclasses.replace(report, results=(failed, *report.results[1:]))

    monkeypatch.setattr(cli, "run_recall_quality", failed_run)
    rc = cli.main(["memory-recall", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert payload["results"][0]["error"] == "provider_member_failed:sqlite"


def test_cli_memory_recall_quality_rejects_nonpositive_limit(capsys):
    cli = _load_cli()
    rc = cli.main(["memory-recall", "--limit", "0"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "--limit must be greater than zero" in captured.err


def test_cli_memory_recall_quality_reports_invalid_fixture(tmp_path, capsys):
    (tmp_path / "cases.json").write_text("not-json")
    (tmp_path / "lessons.json").write_text("[]")
    cli = _load_cli()
    rc = cli.main(["memory-recall", "--fixture", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "invalid recall fixture JSON" in captured.err


def test_cli_memory_recall_quality_reports_missing_and_incomplete_fixtures(tmp_path, capsys):
    cli = _load_cli()
    missing = tmp_path / "missing"
    assert cli.main(["memory-recall", "--fixture", str(missing)]) == 2
    assert "fixture dir not found" in capsys.readouterr().err

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "cases.json").write_text("[]")
    (empty / "lessons.json").write_text("[]")
    assert cli.main(["memory-recall", "--fixture", str(empty)]) == 2
    assert "incomplete recall fixture" in capsys.readouterr().err


def test_cli_memory_recall_quality_uses_custom_fixture_name(tmp_path, capsys):
    source = mb.default_recall_quality_fixture_dir()
    fixture_dir = tmp_path / "custom-recall"
    fixture_dir.mkdir()
    for name in ("cases.json", "lessons.json"):
        (fixture_dir / name).write_text((source / name).read_text())

    cli = _load_cli()
    assert cli.main(["memory-recall", "--fixture", str(fixture_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fixture"] == "custom-recall"
    assert payload["metrics"]["cases"] == 7


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

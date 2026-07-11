"""Well-formedness tests for the harder mem-bench fixture (mem-bench-hard).

This fixture exists because the base mem-bench tasks re-tempt generic Python
gotchas a capable model already avoids, so a real ``--engine`` run can show a
zero repeated-mistake delta even when memory works. The harder fixture plants
*repo-specific conventions* (route work through the ``acme`` platform helpers
instead of the stdlib/``requests`` default) that a model cannot guess without
the seeded lesson, so the memory arm has something real to prevent.

These tests are offline: they load the fixture and run the deterministic stub
A/B (real recall + injection over an in-memory FleetBrain, engine stubbed). They
also mechanically guard the markers so a fixture edit cannot silently produce a
task whose "correct" answer trips its own mistake marker. No model is called, no
network is touched, and no quota is burned.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import memory_benchmark as mb  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "mem-bench-hard"

# The count of tasks flagged repeats_known_mistake; this is the headline N.
EXPECTED_N = 10


def test_hard_fixture_shape():
    fixture = mb.load_fixture(FIXTURE_DIR)
    # Materially larger than the base fixture's N=4: ten convention tasks plus
    # two controls that must not inflate N.
    assert len(fixture.tasks) == 12
    eligible = [t for t in fixture.tasks if t.repeats_known_mistake]
    controls = [t for t in fixture.tasks if not t.repeats_known_mistake]
    assert len(eligible) == EXPECTED_N
    assert {t.task_id for t in controls} == {"add-docstring", "add-type-hint"}


def test_hard_fixture_no_luminik_references():
    # The fixture must read as a neutral acme-org repo; no operator or Luminik
    # identifiers may leak into a public artifact. (Guard operator home paths and
    # Luminik names, not the ``/users/`` API path a task legitimately uses.)
    banned = ("luminik", "/users/batman", "/home/")
    for name in ("tasks.json", "lessons.json", "repo/service.py", "repo/README.md"):
        text = (FIXTURE_DIR / name).read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{name} leaks {token!r}"


def test_fixture_repo_does_not_leak_the_conventions():
    # The whole premise of this fixture is that the convention is UNGUESSABLE
    # from the repo: it lives only in the seeded lessons. If a repo file named
    # the mandated helper, a memory-OFF engine could read it and pass without
    # memory (an early real run showed exactly that). Guard it mechanically:
    # no success-marker pattern and no lesson signal may match any repo file.
    import re

    fixture = mb.load_fixture(FIXTURE_DIR)
    repo_texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((FIXTURE_DIR / "repo").rglob("*"))
        if path.is_file()
    }
    for task in fixture.tasks:
        if not task.repeats_known_mistake:
            continue
        for name, text in repo_texts.items():
            for marker in task.success_markers:
                assert not re.search(marker, text, re.IGNORECASE), (
                    f"repo/{name} leaks the convention behind {task.task_id} "
                    f"(success marker {marker!r} matches)"
                )
            if task.lesson_signal:
                assert task.lesson_signal.lower() not in text.lower(), (
                    f"repo/{name} leaks the lesson signal for {task.task_id}"
                )


def test_every_mistake_task_has_relevant_lesson_and_markers():
    fixture = mb.load_fixture(FIXTURE_DIR)
    lesson_ids = {le.lesson_id for le in fixture.lessons}
    for task in fixture.tasks:
        if not task.repeats_known_mistake:
            continue
        assert task.mistake_markers, f"{task.task_id} has no mistake markers"
        assert task.success_markers, f"{task.task_id} has no success markers"
        assert task.relevant_lesson_ids, f"{task.task_id} declares no relevant lesson"
        for lid in task.relevant_lesson_ids:
            assert lid in lesson_ids, f"{task.task_id} points at missing lesson {lid}"


def test_reference_solutions_are_graded_as_intended():
    # The planted mistaken_solution must trip a mistake marker and the
    # correct_solution must hit a success marker without tripping a mistake one.
    # This is the mechanical guarantee that makes the repeated-mistake-rate real.
    fixture = mb.load_fixture(FIXTURE_DIR)
    for task in fixture.tasks:
        if not task.repeats_known_mistake:
            continue
        made_bad, _ = mb.judge_solution(task, task.mistaken_solution)
        assert made_bad, f"{task.task_id}: mistaken_solution does not trip a mistake marker"

        made_good, ok_good = mb.judge_solution(task, task.correct_solution)
        assert not made_good, f"{task.task_id}: correct_solution trips its own mistake marker"
        assert ok_good, f"{task.task_id}: correct_solution does not hit a success marker"


def test_recall_query_surfaces_the_relevant_lesson():
    # Each task's recall_query must literally match its relevant lesson body so
    # the local FleetBrain recall (literal substring, then recency backfill)
    # returns the right lesson in the top-K. This is what gives recall 100%.
    fixture = mb.load_fixture(FIXTURE_DIR)
    provider = mb.seed_fleet_provider(fixture.lessons, codename=fixture.codename, repo=fixture.repo)
    for task in fixture.tasks:
        if not task.repeats_known_mistake:
            continue
        recalled = mb.default_recall_fn(
            provider, task, fixture.codename, fixture.repo, mb.DEFAULT_RECALL_LIMIT
        )
        recalled_ids = {le.id for le in recalled}
        for lid in task.relevant_lesson_ids:
            assert lid in recalled_ids, f"{task.task_id}: {lid} not recalled for its query"
        context = mb.default_inject_fn(
            provider, task, fixture.codename, fixture.repo, mb.DEFAULT_RECALL_LIMIT
        )
        assert task.lesson_signal.lower() in context.lower()


def test_stub_ab_shows_full_delta_on_hard_fixture():
    fixture = mb.load_fixture(FIXTURE_DIR)
    report = mb.run_memory_ab(fixture, solver=mb.make_stub_solver(), label="hard")

    assert report.memory_off.mistake_eligible == EXPECTED_N
    assert report.memory_on.mistake_eligible == EXPECTED_N

    # Ceiling behaviour: OFF repeats every planted mistake, ON repeats none.
    assert report.memory_off.repeated_mistake_rate == pytest.approx(1.0)
    assert report.memory_on.repeated_mistake_rate == pytest.approx(0.0)
    assert report.repeated_mistake_rate_delta == pytest.approx(1.0)

    # Retrieval: the right lesson is recalled for all ten tasks; distractors in
    # the top-3 keep precision at 1/3 (one relevant of three recalled per task).
    assert report.memory_on.retrieval.recall == pytest.approx(1.0)
    assert report.memory_on.retrieval.recalled_relevant == EXPECTED_N
    assert report.memory_on.retrieval.precision == pytest.approx(1 / 3, abs=1e-3)

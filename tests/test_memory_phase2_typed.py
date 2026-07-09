"""Phase 2 memory: typed lessons, code-grounding anchors, validity, repo profile.

Covers, additively, the four Phase 2 features on top of the Phase 1 stores:

* typed lessons (``kind``) on both the SQLite hybrid store and FleetBrain, with
  a sensible default for untyped/legacy lessons (backward compatibility);
* code-grounding anchors: a lesson->file/symbol/node/lesson link, and the
  anchor join that surfaces "editing this file -> the lessons about it";
* bi-temporal validity: supersede invalidates (never deletes) and recall stops
  surfacing a superseded/expired lesson;
* the deterministic, budget-aware repo-profile injector.

The hybrid store cases run with and without FTS5 (a ``_fts_ok=False`` variant
exercises the LIKE fallback); the dense (sqlite-vec) arm is not required.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

from agent_runner import memory_ranking  # noqa: E402
from agent_runner.repo_profile import (  # noqa: E402
    RepoProfile,
    build_repo_profile,
    format_repo_profile_block,
    repo_profile_block,
    repo_profile_enabled,
)
from fleet_brain import FleetBrain, Lesson, SQLiteStore, normalize_kind  # noqa: E402
from fleet_brain.taxonomy import (  # noqa: E402
    DEFAULT_LESSON_KIND,
    LESSON_KINDS,
    kind_recall_bonus,
)
from memory.sqlite_hybrid import SqliteHybridProvider  # noqa: E402

# --------------------------------------------------------------------------
# taxonomy
# --------------------------------------------------------------------------


def test_normalize_kind_folds_aliases_and_unknowns() -> None:
    assert normalize_kind("convention") == "convention"
    assert normalize_kind("BugFix") == "fix"
    assert normalize_kind("adr") == "decision"
    assert normalize_kind("review") == "review-pattern"
    assert normalize_kind("") == DEFAULT_LESSON_KIND
    assert normalize_kind(None) == DEFAULT_LESSON_KIND
    assert normalize_kind("something-made-up") == DEFAULT_LESSON_KIND


def test_kind_recall_preference_orders_conventions_first() -> None:
    assert kind_recall_bonus("convention") > kind_recall_bonus("fix")
    assert kind_recall_bonus("fix") > kind_recall_bonus("note")
    assert kind_recall_bonus("note") == 0.0
    assert "note" in LESSON_KINDS


# --------------------------------------------------------------------------
# hybrid store: typed lessons
# --------------------------------------------------------------------------


@pytest.fixture
def hybrid() -> SqliteHybridProvider:
    return SqliteHybridProvider(db_path=Path(":memory:"))


def test_hybrid_reflect_persists_kind_and_provenance(hybrid: SqliteHybridProvider) -> None:
    lesson = hybrid.reflect(
        codename="lucius",
        repo="acme/api",
        body="auth uses JWT in the middleware",
        kind="convention",
        provenance="firing-42",
    )
    assert lesson.kind == "convention"
    assert lesson.provenance == "firing-42"
    (round_tripped,) = hybrid.list_lessons(limit=1)
    assert round_tripped.kind == "convention"
    assert round_tripped.provenance == "firing-42"


def test_hybrid_untyped_lesson_defaults_to_note(hybrid: SqliteHybridProvider) -> None:
    lesson = hybrid.reflect(codename="lucius", repo="acme/api", body="a plain lesson")
    assert lesson.kind == DEFAULT_LESSON_KIND
    assert lesson.provenance is None
    recalled = hybrid.recall(codename="lucius", repo="acme/api", limit=5)
    assert recalled and recalled[0].kind == DEFAULT_LESSON_KIND


def test_hybrid_provenance_defaults_to_firing_id(hybrid: SqliteHybridProvider) -> None:
    lesson = hybrid.reflect(codename="lucius", repo="acme/api", body="body", firing_id="firing-7")
    assert lesson.provenance == "firing-7"


# --------------------------------------------------------------------------
# hybrid store: anchors + code-grounding
# --------------------------------------------------------------------------


def _seed_anchored(hybrid: SqliteHybridProvider) -> tuple[Lesson, Lesson, Lesson]:
    conv = hybrid.reflect(
        codename="lucius",
        repo="acme/api",
        body="auth uses JWT verified in the middleware",
        kind="convention",
        anchors=[("file", "acme/api/auth.py")],
    )
    fix = hybrid.reflect(
        codename="lucius",
        repo="acme/api",
        body="null deref when token missing was fixed by guarding early",
        kind="fix",
        anchors=[("file", "acme/api/auth.py")],
    )
    other = hybrid.reflect(
        codename="lucius", repo="acme/api", body="unrelated readme note", kind="note"
    )
    return conv, fix, other


def test_hybrid_lessons_for_anchor_returns_only_anchored(hybrid: SqliteHybridProvider) -> None:
    conv, fix, other = _seed_anchored(hybrid)
    hits = hybrid.lessons_for_anchor(anchor_ref="acme/api/auth.py", repo="acme/api")
    ids = {lesson.id for lesson in hits}
    assert ids == {conv.id, fix.id}
    assert other.id not in ids


def test_hybrid_anchor_grounded_recall_surfaces_anchored_first(
    hybrid: SqliteHybridProvider,
) -> None:
    conv, fix, _other = _seed_anchored(hybrid)
    # A query that lexically matches the unrelated note; without anchoring it
    # would lead. With anchor_refs, the file's lessons are surfaced first.
    recalled = hybrid.recall(
        codename="lucius",
        repo="acme/api",
        query="readme",
        limit=3,
        anchor_refs=["acme/api/auth.py"],
    )
    leading = {lesson.id for lesson in recalled[:2]}
    assert leading == {conv.id, fix.id}


def test_hybrid_add_anchor_is_idempotent(hybrid: SqliteHybridProvider) -> None:
    lesson = hybrid.reflect(codename="lucius", repo="acme/api", body="body", kind="fix")
    assert hybrid.add_anchor(lesson_id=lesson.id, anchor_ref="acme/api/x.py") is True
    assert hybrid.add_anchor(lesson_id=lesson.id, anchor_ref="acme/api/x.py") is True
    hits = hybrid.lessons_for_anchor(anchor_ref="acme/api/x.py", repo="acme/api")
    assert len(hits) == 1
    assert hybrid.add_anchor(lesson_id="", anchor_ref="") is False


def test_hybrid_forget_removes_anchors(hybrid: SqliteHybridProvider) -> None:
    lesson = hybrid.reflect(
        codename="lucius",
        repo="acme/api",
        body="body",
        anchors=[("file", "acme/api/x.py")],
    )
    assert hybrid.forget_lesson(lesson.id) is True
    assert hybrid.lessons_for_anchor(anchor_ref="acme/api/x.py") == []


# --------------------------------------------------------------------------
# hybrid store: validity / supersede
# --------------------------------------------------------------------------


def test_hybrid_supersede_invalidates_old_lesson(hybrid: SqliteHybridProvider) -> None:
    old = hybrid.reflect(
        codename="lucius", repo="acme/api", body="old convention: tabs", kind="convention"
    )
    new = hybrid.reflect(
        codename="lucius", repo="acme/api", body="new convention: spaces", kind="convention"
    )
    assert hybrid.supersede_lesson(old.id, new.id) is True
    recalled = {lesson.id for lesson in hybrid.recall(codename="lucius", repo="acme/api", limit=10)}
    assert old.id not in recalled  # invalidated
    assert new.id in recalled  # still valid
    # invalidate, not delete: the row survives for audit
    assert any(lesson.id == old.id for lesson in hybrid.list_lessons(limit=10))


def test_hybrid_supersede_records_lesson_link(hybrid: SqliteHybridProvider) -> None:
    old = hybrid.reflect(codename="lucius", repo="acme/api", body="old", kind="fix")
    new = hybrid.reflect(codename="lucius", repo="acme/api", body="new", kind="fix")
    hybrid.supersede_lesson(old.id, new.id)
    # the new lesson carries a supersedes anchor to the old id
    linked = hybrid.lessons_for_anchor(anchor_ref=old.id, anchor_type="lesson")
    assert any(lesson.id == new.id for lesson in linked)


def test_hybrid_supersede_blank_is_noop(hybrid: SqliteHybridProvider) -> None:
    assert hybrid.supersede_lesson("", "x") is False
    assert hybrid.supersede_lesson("x", "x") is False
    assert hybrid.supersede_lesson("missing", "also-missing") is False


def test_hybrid_expired_valid_until_is_not_recalled(hybrid: SqliteHybridProvider) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    lesson = Lesson(
        id="expired-1",
        codename="lucius",
        repo="acme/api",
        body="an expired lesson",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
        valid_until=past,
    )
    # write directly through the internal path to set validity
    with hybrid._connect() as conn, conn:
        hybrid._write_lesson(conn, lesson)
    assert hybrid.recall(codename="lucius", repo="acme/api", limit=10) == []


def test_hybrid_validity_filter_holds_without_fts(hybrid: SqliteHybridProvider) -> None:
    # Force the LIKE fallback (no FTS5) and confirm supersede filtering still holds.
    hybrid._fts_ok = False
    old = hybrid.reflect(codename="lucius", repo="acme/api", body="graphql schema here")
    new = hybrid.reflect(codename="lucius", repo="acme/api", body="graphql schema moved")
    hybrid.supersede_lesson(old.id, new.id)
    recalled = {
        lesson.id
        for lesson in hybrid.recall(codename="lucius", repo="acme/api", query="graphql", limit=10)
    }
    assert old.id not in recalled
    assert new.id in recalled


# --------------------------------------------------------------------------
# FleetBrain store: parity
# --------------------------------------------------------------------------


@pytest.fixture
def brain() -> FleetBrain:
    return FleetBrain(store=SQLiteStore(db_path=Path(":memory:")))


def test_brain_reflect_types_and_defaults(brain: FleetBrain) -> None:
    typed = brain.reflect(codename="lucius", repo="acme/api", body="a decision", kind="decision")
    assert typed.kind == "decision"
    untyped = brain.reflect(codename="lucius", repo="acme/api", body="legacy-style lesson")
    assert untyped.kind == DEFAULT_LESSON_KIND


def test_brain_anchor_and_lookup(brain: FleetBrain) -> None:
    lesson = brain.reflect(
        codename="lucius", repo="acme/api", body="convention about auth", kind="convention"
    )
    brain.anchor_lesson(lesson_id=lesson.id, anchor_ref="acme/api/auth.py", repo="acme/api")
    hits = brain.lessons_for_anchor(anchor_ref="acme/api/auth.py", repo="acme/api")
    assert [lesson.id] == [h.id for h in hits]


def test_brain_supersede_invalidates(brain: FleetBrain) -> None:
    old = brain.reflect(codename="lucius", repo="acme/api", body="old rule", kind="convention")
    new = brain.reflect(codename="lucius", repo="acme/api", body="new rule", kind="convention")
    assert brain.supersede_lesson(old_id=old.id, new_id=new.id) is True
    recalled = {lesson.id for lesson in brain.store.recall_lessons("lucius", "acme/api", limit=10)}
    assert old.id not in recalled
    assert new.id in recalled
    assert brain.store.get_lesson(old.id) is not None  # not deleted


def test_brain_backward_compat_untyped_row_reads_note(brain: FleetBrain) -> None:
    # Simulate a pre-Phase-2 row by inserting through the store with a lesson
    # whose kind is the default, then confirm recall reads a valid kind.
    brain.reflect(codename="lucius", repo="acme/api", body="pre-phase-2")
    (lesson,) = brain.store.recall_lessons("lucius", "acme/api", limit=1)
    assert lesson.kind == DEFAULT_LESSON_KIND


# --------------------------------------------------------------------------
# type-aware recall ranking
# --------------------------------------------------------------------------


def _pair(kind: str) -> tuple[Lesson, float | None]:
    lesson = Lesson(
        id=f"id-{kind}",
        codename="lucius",
        repo="acme/api",
        body=f"a {kind} lesson",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
        kind=kind,
    )
    return (lesson, 0.5)


def test_typed_recall_off_by_default_preserves_order() -> None:
    pairs = [_pair("note"), _pair("convention"), _pair("fix")]
    assert memory_ranking.apply_typed_recall(pairs, env={}) == pairs


def test_typed_recall_lifts_conventions_and_fixes() -> None:
    pairs = [_pair("note"), _pair("fix"), _pair("convention")]
    ordered = memory_ranking.apply_typed_recall(pairs, env={"ALFRED_MEMORY_TYPED_RECALL": "1"})
    kinds = [lesson.kind for lesson, _ in ordered]
    assert kinds[0] == "convention"
    assert kinds[1] == "fix"
    assert kinds[2] == "note"


# --------------------------------------------------------------------------
# repo-profile injector
# --------------------------------------------------------------------------


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n", encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tpytest\nlint:\n\truff check\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("be careful\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()  # must be skipped in structure
    (tmp_path / "node_modules").mkdir()  # must be skipped
    return tmp_path


def test_repo_profile_is_deterministic(sample_repo: Path) -> None:
    first = format_repo_profile_block(build_repo_profile(sample_repo), budget=2000)
    second = format_repo_profile_block(build_repo_profile(sample_repo), budget=2000)
    assert first == second
    assert first != ""


def test_repo_profile_detects_facts(sample_repo: Path) -> None:
    profile = build_repo_profile(sample_repo)
    assert profile is not None
    assert "pyproject.toml" in profile.manifests
    assert "uv" in profile.package_managers
    assert "pytest" in profile.verify_commands
    assert any(cmd.startswith("make ") for cmd in profile.verify_commands)
    assert "AGENTS.md" in profile.context_files
    assert "lib" in profile.structure
    assert "tests" in profile.structure
    assert ".git" not in profile.structure
    assert "node_modules" not in profile.structure


def test_repo_profile_block_gated_off_by_default(sample_repo: Path) -> None:
    assert repo_profile_enabled(env={}) is False
    assert repo_profile_block(sample_repo, env={}) == ""


def test_repo_profile_block_armed(sample_repo: Path) -> None:
    block = repo_profile_block(sample_repo, env={"ALFRED_REPO_PROFILE": "1"})
    assert block.startswith("Repo profile")
    assert "pytest" in block


def test_repo_profile_budget_is_respected(sample_repo: Path) -> None:
    profile = build_repo_profile(sample_repo)
    tiny = format_repo_profile_block(profile, budget=20)
    assert tiny == ""  # header + a fact cannot fit -> nothing
    bounded = format_repo_profile_block(profile, budget=120)
    assert len(bounded) <= 120


def test_repo_profile_missing_root_is_none() -> None:
    assert build_repo_profile("/does/not/exist/anywhere-123") is None
    assert format_repo_profile_block(None) == ""
    assert format_repo_profile_block(RepoProfile(root=Path("/x"))) == ""

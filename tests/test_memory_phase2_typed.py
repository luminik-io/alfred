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
from fleet_brain import (  # noqa: E402
    FleetBrain,
    Lesson,
    MemoryPromotionError,
    SQLiteStore,
    normalize_kind,
)
from fleet_brain.taxonomy import (  # noqa: E402
    DEFAULT_LESSON_KIND,
    LESSON_KINDS,
    OPS_TAG,
    is_ops_lesson,
    kind_recall_bonus,
)
from memory.providers import (  # noqa: E402
    ChainedMemoryProvider,
    FleetBrainProvider,
    NullMemoryProvider,
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


def test_is_ops_lesson_detects_runtime_markers() -> None:
    # Explicit ops tag, the harvest marker, and any runtime class all read ops.
    assert is_ops_lesson([OPS_TAG])
    assert is_ops_lesson(["failure-pattern", "pattern:bane|-|x|-"])
    assert is_ops_lesson(["auto-harvest", "class:provider_limit"])
    assert is_ops_lesson(["class:auth"])
    assert is_ops_lesson(("class:TIMEOUT",))  # case-insensitive, tuple ok


def test_is_ops_lesson_leaves_codebase_lessons_alone() -> None:
    # Codebase lessons (conventions, fixes, review patterns) are never ops, and
    # an unclassified failure is NOT asserted to be a runtime issue.
    assert not is_ops_lesson(["tests", "graphql"])
    assert not is_ops_lesson(["class:unknown"])
    assert not is_ops_lesson(["review-pattern"])
    # Defensive: a non-iterable or malformed tags value never raises.
    assert not is_ops_lesson(None)
    assert not is_ops_lesson("failure-pattern")  # a bare string is not a tag list
    assert not is_ops_lesson([None, 123])


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


# --------------------------------------------------------------------------
# P1: promotion errors must not be swallowed (signature-checked fallback)
# --------------------------------------------------------------------------


class _Phase2Writer:
    """A writer whose reflect DOES accept the Phase 2 kwargs but raises inside."""

    name = "phase2-raising"

    def __init__(self) -> None:
        self.calls = 0

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags=None,
        severity="info",
        firing_id=None,
        created_at=None,
        memory_id=None,
        kind=None,
        provenance=None,
    ) -> Lesson:
        self.calls += 1
        raise TypeError("internal bug unrelated to the signature")


class _Phase1Writer:
    """A writer that predates the Phase 2 kwargs (Phase 1 contract only)."""

    name = "phase1"

    def __init__(self) -> None:
        self.calls = 0
        self.last_kwargs: dict[str, object] | None = None

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags=None,
        severity="info",
        firing_id=None,
        created_at=None,
        memory_id=None,
    ) -> Lesson:
        self.calls += 1
        self.last_kwargs = {"memory_id": memory_id}
        return Lesson(
            id=memory_id or "x",
            codename=codename,
            repo=repo,
            body=body,
            tags=list(tags or []),
            created_at=datetime.now(UTC),
            firing_id=firing_id,
            severity=severity,
        )


def _candidate(brain: FleetBrain, body: str) -> str:
    cand = brain.propose_memory(
        codename="lucius",
        repo="acme/api",
        body=body,
        evidence="seen twice",
        confidence=0.9,
        kind="fix",
    )
    return cand.id


def test_promote_does_not_mask_internal_typeerror(brain: FleetBrain) -> None:
    # A writer that accepts kind/provenance but raises TypeError internally must
    # surface as a MemoryPromotionError and be called exactly once (never retried
    # without the Phase 2 kwargs, which would mask the real bug).
    writer = _Phase2Writer()
    cid = _candidate(brain, "a real fix")
    with pytest.raises(MemoryPromotionError):
        brain.promote_memory_candidate(cid, lesson_writer=writer)
    assert writer.calls == 1
    # Candidate stays pending (re-promotable), not silently validated.
    assert brain.store.get_memory_candidate(cid).status == "candidate"


def test_promote_falls_back_for_phase1_writer(brain: FleetBrain) -> None:
    # A Phase-1-only writer (no kind/provenance) still promotes: the signature
    # check skips the Phase 2 kwargs rather than erroring.
    writer = _Phase1Writer()
    cid = _candidate(brain, "a fix a phase-1 writer can store")
    lesson = brain.promote_memory_candidate(cid, lesson_writer=writer)
    assert writer.calls == 1
    assert lesson.id == _lesson_memory_id_for(cid)
    assert brain.store.get_memory_candidate(cid).status == "validated"


def _lesson_memory_id_for(candidate_id: str) -> str:
    from fleet_brain import candidate_id_from_lesson_id

    # Round-trip helper: the promote path derives a deterministic memory id from
    # the candidate id; recover it the same way the retire path does.
    prefix = "lesson:memory_candidate:"
    assert candidate_id_from_lesson_id(prefix + candidate_id) == candidate_id
    return prefix + candidate_id


# --------------------------------------------------------------------------
# P1: anchors must round-trip through export / import
# --------------------------------------------------------------------------


def test_export_includes_anchors_and_import_restores_them() -> None:
    source = FleetBrain(store=SQLiteStore(db_path=Path(":memory:")))
    lesson = source.reflect(
        codename="lucius", repo="acme/api", body="auth convention", kind="convention"
    )
    source.anchor_lesson(lesson_id=lesson.id, anchor_ref="acme/api/auth.py", repo="acme/api")

    snapshot = source.export()
    assert "lesson_anchors" in snapshot
    assert any(a["anchor_ref"] == "acme/api/auth.py" for a in snapshot["lesson_anchors"])

    target = FleetBrain(store=SQLiteStore(db_path=Path(":memory:")))
    counts = target.import_snapshot(snapshot)
    assert counts["lessons"] == 1
    assert counts["lesson_anchors"] == 1
    restored = target.lessons_for_anchor(anchor_ref="acme/api/auth.py", repo="acme/api")
    assert [lesson.id] == [r.id for r in restored]
    # the lesson's typed kind survived the round-trip too
    assert restored[0].kind == "convention"


# --------------------------------------------------------------------------
# P1: anchor_refs recall must work THROUGH the chained provider / protocol
# --------------------------------------------------------------------------


def test_anchor_refs_recall_through_chained_provider() -> None:
    sqlite = SqliteHybridProvider(db_path=Path(":memory:"))
    conv = sqlite.reflect(
        codename="lucius",
        repo="acme/api",
        body="auth uses JWT verified in the middleware",
        kind="convention",
        anchors=[("file", "acme/api/auth.py")],
    )
    sqlite.reflect(codename="lucius", repo="acme/api", body="unrelated readme note", kind="note")
    # A fleet ledger with nothing anchored, chained AFTER the sqlite store.
    fleet = FleetBrainProvider(brain=FleetBrain(store=SQLiteStore(db_path=Path(":memory:"))))
    chain = ChainedMemoryProvider(providers=[sqlite, fleet, NullMemoryProvider()])

    # anchor_refs must reach the sqlite member through the chain; without the
    # plumbing this raised TypeError or silently ignored the anchor.
    recalled = chain.recall(
        codename="lucius",
        repo="acme/api",
        query="readme",
        limit=3,
        anchor_refs=["acme/api/auth.py"],
    )
    assert conv.id in {lesson.id for lesson in recalled}
    assert recalled[0].id == conv.id


def test_fleet_provider_honors_anchor_refs() -> None:
    brain = FleetBrain(store=SQLiteStore(db_path=Path(":memory:")))
    lesson = brain.reflect(codename="lucius", repo="acme/api", body="anchored fix", kind="fix")
    brain.anchor_lesson(lesson_id=lesson.id, anchor_ref="acme/api/auth.py", repo="acme/api")
    brain.reflect(codename="lucius", repo="acme/api", body="plain note", kind="note")
    provider = FleetBrainProvider(brain=brain)
    recalled = provider.recall(
        codename="lucius", repo="acme/api", limit=3, anchor_refs=["acme/api/auth.py"]
    )
    assert recalled[0].id == lesson.id


# --------------------------------------------------------------------------
# P1: runtime recall must actually pass anchor_refs (feature not inert)
# --------------------------------------------------------------------------


def test_derive_anchor_refs_emits_bare_and_repo_qualified_variants() -> None:
    from agent_runner.memory_runtime import derive_anchor_refs

    refs = derive_anchor_refs(["src/auth.py", "/src/db.py"], repo="acme/api")
    assert "src/auth.py" in refs
    assert "acme/api/src/auth.py" in refs  # repo-qualified variant
    assert "src/db.py" in refs
    # already-qualified paths are not double-prefixed
    assert derive_anchor_refs(["acme/api/x.py"], repo="acme/api") == ["acme/api/x.py"]
    # no file signal -> empty (never fabricates a path)
    assert derive_anchor_refs([], repo="acme/api") == []
    assert derive_anchor_refs(None, repo="acme/api") == []


def _seed_runtime_provider() -> SqliteHybridProvider:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="auth uses JWT verified in the middleware",
        kind="convention",
        anchors=[("file", "acme/api/src/auth.py")],
    )
    # A generic lesson that lexically matches the query, so WITHOUT anchoring it
    # leads the recall order.
    provider.reflect(
        codename="lucius", repo="acme/api", body="general readme housekeeping note", kind="note"
    )
    return provider


def test_runtime_anchor_recall_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_runner.memory_runtime import anchor_recall_enabled, with_memory_prompt

    monkeypatch.delenv("ALFRED_MEMORY_ANCHOR_RECALL", raising=False)
    assert anchor_recall_enabled() is False
    provider = _seed_runtime_provider()
    out = with_memory_prompt(
        "TASK",
        provider,
        codename="lucius",
        repo="acme/api",
        query="readme",
        limit=3,
        orientation_paths=["src/auth.py"],
    )
    # Flag off: no anchor derivation. Only the query-matched note is recalled;
    # the file-linked convention (which needs its anchor to surface) is absent.
    assert "general readme housekeeping" in out
    assert "auth uses JWT" not in out


def test_runtime_anchor_recall_surfaces_file_linked_lesson_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner.memory_runtime import with_memory_prompt

    monkeypatch.setenv("ALFRED_MEMORY_ANCHOR_RECALL", "1")
    provider = _seed_runtime_provider()
    out = with_memory_prompt(
        "TASK",
        provider,
        codename="lucius",
        repo="acme/api",
        query="readme",
        limit=3,
        # The firing's orientation paths carry the file context; the anchored
        # lesson is linked to acme/api/src/auth.py (repo-qualified variant).
        orientation_paths=["src/auth.py"],
    )
    assert "auth uses JWT" in out
    # The file-linked convention now leads the generic note.
    assert out.index("auth uses JWT") < out.index("general readme housekeeping")


def test_runtime_anchor_recall_through_chained_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner.memory_runtime import format_memory_context

    monkeypatch.setenv("ALFRED_MEMORY_ANCHOR_RECALL", "1")
    sqlite = _seed_runtime_provider()
    # A no-op member that does NOT accept anchor_refs must not break the chain.
    chain = ChainedMemoryProvider(providers=[sqlite, NullMemoryProvider()])
    block = format_memory_context(
        chain,
        codename="lucius",
        repo="acme/api",
        query="readme",
        limit=3,
        anchor_refs=["acme/api/src/auth.py"],
    )
    assert "auth uses JWT" in block
    assert block.index("auth uses JWT") < block.index("general readme housekeeping")


class _ScoredStub:
    """A scored chain member (Redis-like): high-scored generic hit, no anchors."""

    name = "scored"

    def __init__(self, lesson: Lesson) -> None:
        self._lesson = lesson

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs=None,
    ) -> list[Lesson]:
        return [self._lesson]

    def recall_scored(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
    ) -> list[tuple[Lesson, float | None]]:
        return [(self._lesson, 0.99)]


def test_mixed_chain_hoists_anchored_lesson_ahead_of_scored_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner.memory_runtime import format_memory_context

    generic = Lesson(
        id="scored-generic-1",
        codename="lucius",
        repo="acme/api",
        body="scored generic hit about the flow",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
        kind="note",
    )
    sqlite = SqliteHybridProvider(db_path=Path(":memory:"))
    sqlite.reflect(
        codename="lucius",
        repo="acme/api",
        body="auth uses JWT verified in the middleware",
        kind="convention",
        anchors=[("file", "acme/api/src/auth.py")],
    )
    # Scored member FIRST in the chain, so its high-scored generic hit is merged
    # ahead of the non-scored member's anchored lesson.
    chain = ChainedMemoryProvider(providers=[_ScoredStub(generic), sqlite])

    # ON: the anchored lesson must lead despite the scored generic hit.
    monkeypatch.setenv("ALFRED_MEMORY_ANCHOR_RECALL", "1")
    on = format_memory_context(
        chain,
        codename="lucius",
        repo="acme/api",
        query="flow",
        limit=3,
        anchor_refs=["acme/api/src/auth.py"],
    )
    assert "auth uses JWT" in on
    assert "scored generic hit" in on
    assert on.index("auth uses JWT") < on.index("scored generic hit")

    # OFF: no anchor_refs, ordering unchanged (scored generic hit leads).
    off = format_memory_context(
        chain,
        codename="lucius",
        repo="acme/api",
        query="flow",
        limit=3,
        anchor_refs=None,
    )
    assert "scored generic hit" in off
    if "auth uses JWT" in off:
        assert off.index("scored generic hit") < off.index("auth uses JWT")


class _ListProvider:
    """A plain (non-scored) provider that returns a fixed lesson list."""

    name = "list"

    def __init__(self, lessons: list[Lesson]) -> None:
        self._lessons = lessons

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs=None,
    ) -> list[Lesson]:
        return list(self._lessons)


def test_gated_pairs_dedup_prefers_anchored_copy_on_body_tie() -> None:
    from agent_runner.memory_runtime import _gated_pairs

    now = datetime.now(UTC)
    a = Lesson(
        id="A",
        codename="lucius",
        repo="acme/api",
        body="shared body",
        tags=[],
        created_at=now,
        firing_id=None,
    )
    b = Lesson(
        id="B",
        codename="lucius",
        repo="acme/api",
        body="shared body",
        tags=[],
        created_at=now,
        firing_id=None,
    )
    provider = _ListProvider([a, b])  # A (not anchored) before B (anchored)
    # Anchored copy B wins the duplicate-body tie so the hoist can promote it.
    on = _gated_pairs(
        provider,
        codename="lucius",
        repo="acme/api",
        query=None,
        limit=5,
        threshold=0.0,
        anchored_ids={"B"},
    )
    assert [p[0].id for p in on] == ["B"]
    # Anchor recall off (empty set): dedup keeps the first (A), unchanged.
    off = _gated_pairs(
        provider,
        codename="lucius",
        repo="acme/api",
        query=None,
        limit=5,
        threshold=0.0,
        anchored_ids=set(),
    )
    assert [p[0].id for p in off] == ["A"]


def test_mixed_chain_dedup_keeps_and_hoists_anchored_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner.memory_runtime import format_memory_context

    shared = "shared lesson about the auth subsystem"
    # Same BODY in the scored member (id, not anchored) and the file store
    # (anchored), distinguished only by tags so the survivor is identifiable.
    generic = Lesson(
        id="scored-dup-1",
        codename="lucius",
        repo="acme/api",
        body=shared,
        tags=["scored-copy"],
        created_at=datetime.now(UTC),
        firing_id=None,
        kind="note",
    )
    sqlite = SqliteHybridProvider(db_path=Path(":memory:"))
    sqlite.reflect(
        codename="lucius",
        repo="acme/api",
        body=shared,
        tags=["anchored-copy"],
        kind="convention",
        anchors=[("file", "acme/api/src/auth.py")],
    )
    chain = ChainedMemoryProvider(providers=[_ScoredStub(generic), sqlite])

    # ON: the anchored copy wins the body tie and is the survivor that leads.
    monkeypatch.setenv("ALFRED_MEMORY_ANCHOR_RECALL", "1")
    on = format_memory_context(
        chain,
        codename="lucius",
        repo="acme/api",
        query="auth",
        limit=3,
        anchor_refs=["acme/api/src/auth.py"],
    )
    assert "anchored-copy" in on
    assert "scored-copy" not in on  # the scored duplicate was dropped for the anchored one

    # OFF: dedup keeps the first (scored) copy, as before.
    off = format_memory_context(
        chain,
        codename="lucius",
        repo="acme/api",
        query="auth",
        limit=3,
        anchor_refs=None,
    )
    assert "scored-copy" in off
    assert "anchored-copy" not in off
